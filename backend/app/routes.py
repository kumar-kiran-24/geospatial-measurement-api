"""HTTP routes for the Geospatial File Measurement API."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request, status, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from pyproj import CRS, Transformer
from shapely.geometry import shape, mapping

from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.enums import FileFormat, MeasurementStatus, MeasurementType, ProcessingStatus
from app.models.file_record import FileRecord
from app.models.feature_measurement import FeatureMeasurement
from app.schemas.common import PaginationMeta
from app.schemas.files import FileDetail, FileListItem, FileListResponse, FileUploadResponse
from app.schemas.measurements import FeatureMeasurementOut, FileMeasurementsResponse, MeasurementSummary, AreaMeasurement, LengthMeasurement
from app.services.crs_service import CrsService
from app.services.file_storage_service import FileStorageService, StoredFile
from app.services.file_validation_service import FileValidationService
from app.services.geospatial_reader import GeospatialReader, ReadDataset
from app.services.processing_engine import ProcessingEngine
from app.utils.archive import inspect_zip, extract_archive

logger = get_logger(__name__)

api_router = APIRouter()


# --------------------------------------------------------------------------- #
# File collection (history / detail / delete)
# --------------------------------------------------------------------------- #
@api_router.get("/files/", response_model=FileListResponse)
async def list_files(
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
) -> FileListResponse:
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 50
    offset = (page - 1) * page_size
    total = db.query(func.count(FileRecord.id)).scalar() or 0
    records = (
        db.query(FileRecord)
        .order_by(FileRecord.created_at.desc(), FileRecord.id.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
    items = [FileListItem.model_validate(record) for record in records]
    pagination = PaginationMeta.build(page=page, page_size=page_size, total_items=total)
    return FileListResponse(items=items, pagination=pagination)


@api_router.get("/files/{file_id}/", response_model=FileDetail)
async def file_detail(file_id: str, db: Session = Depends(get_db)) -> FileDetail:
    record = db.get(FileRecord, file_id)
    if record is None:
        raise NotFoundError("File not found.")
    return FileDetail.model_validate(record)


@api_router.delete("/files/{file_id}/")
async def delete_file(file_id: str, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    record = db.get(FileRecord, file_id)
    if record is None:
        raise NotFoundError("File not found.")
    # Delete DB records first; cascade will remove features
    db.delete(record)
    db.commit()
    settings = request.app.state.settings
    storage = FileStorageService(settings.storage_root, settings.max_upload_size_bytes)
    try:
        storage.delete_file(file_id)
    except Exception:
        # Log but do not roll back DB – storage cleanup is best effort
        pass
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content="")


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #
@api_router.post("/files/", response_model=FileUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    file: UploadFile,
    db: Session = Depends(get_db),
) -> FileUploadResponse:
    settings: Settings = request.app.state.settings
    storage = FileStorageService(settings.storage_root, settings.max_upload_size_bytes)
    validator = FileValidationService(settings)

    stored: StoredFile = await storage.save_upload(file, file.filename or "upload")
    try:
        extension = validator.validate_extension(stored.original_filename)
        format_detected = validator.sniff(Path(stored.absolute_path), extension)

        if format_detected is FileFormat.SHAPEFILE:
            inspection = inspect_zip(
                Path(stored.absolute_path),
                max_entries=settings.max_zip_entries,
                max_uncompressed_bytes=settings.max_zip_uncompressed_bytes,
                max_compression_ratio=settings.max_zip_compression_ratio,
                max_member_bytes=settings.max_archive_member_bytes,
            )
            # Validate Shapefile contents
            validator.validate_shapefile_archive(inspection)
            extraction_dir = Path(storage.extraction_directory(stored.file_id))
            extract_archive(Path(stored.absolute_path), extraction_dir, inspection)
            shp_path = validator.find_shp_file(extraction_dir)
            reader = GeospatialReader(max_features=settings.max_features_per_file)
            dataset = reader.read(shp_path, FileFormat.SHAPEFILE)
            # Cleanup extraction after reading
            storage.cleanup_extraction(stored.file_id)
        else:
            reader = GeospatialReader(max_features=settings.max_features_per_file)
            dataset = reader.read(Path(stored.absolute_path), FileFormat.KML)
    except Exception:
        storage.cleanup_extraction(stored.file_id)
        storage.delete_file(stored.file_id)
        raise

    # Create file record
    record = FileRecord(
        id=stored.file_id,
        original_filename=stored.original_filename,
        storage_path=stored.relative_path,
        file_format=format_detected.value,
        content_type=file.content_type,
        size_bytes=stored.size_bytes,
        checksum_sha256=stored.checksum_sha256,
        status=ProcessingStatus.PENDING.value,
    )
    db.add(record)
    db.flush()

    # Process
    try:
        record.status = ProcessingStatus.PROCESSING.value
        record.processing_started_at = datetime.now(timezone.utc)
        db.add(record)
        db.flush()

        engine = ProcessingEngine(CrsService(), settings, validator, storage)
        measurements = engine.process(dataset, str(record.id))

        # Update file metadata from dataset
        if dataset.source_crs is not None:
            record.source_crs = dataset.source_crs.to_wkt()
            try:
                epsg = dataset.source_crs.to_epsg()
                record.source_crs_name = f"EPSG:{epsg}"
            except Exception:
                record.source_crs_name = dataset.source_crs.name
            # Determine kind
            from app.services.crs_service import CrsService as CS
            kind = CS.classify(dataset.source_crs)
            record.source_crs_kind = kind.value
        else:
            record.source_crs_kind = "unknown"

        # Build measurement plan to capture strategy
        plan = CrsService().build_plan(dataset.source_crs, dataset.bounds)
        record.measurement_strategy = plan.strategy.value
        if plan.planar_crs is not None:
            record.measurement_crs = plan.planar_crs.to_wkt()

        # Persist measurements
        for idx, m in enumerate(measurements):
            m.file_id = record.id
            m.feature_index = idx
        db.add_all(measurements)

        record.status = ProcessingStatus.COMPLETED.value
        record.feature_count = len(measurements)
        record.processing_completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(record)
        return FileUploadResponse.model_validate(record)
    except Exception as exc:
        db.rollback()
        # Persist failure in a separate, deliberate transaction
        try:
            record.status = ProcessingStatus.FAILED.value
            record.error_code = type(exc).__name__
            record.error_message = str(exc)[:1000]
            record.processing_completed_at = datetime.now(timezone.utc)
            db.add(record)
            db.commit()
        except Exception:
            db.rollback()
        # Idempotent cleanup
        try:
            storage.cleanup_extraction(stored.file_id)
        except Exception:
            pass
        raise


# --------------------------------------------------------------------------- #
# Measurements
# --------------------------------------------------------------------------- #
@api_router.get("/files/{file_id}/measurements/", response_model=FileMeasurementsResponse)
async def file_measurements(file_id: str, page: int = 1, page_size: int = 50, db: Session = Depends(get_db)) -> FileMeasurementsResponse:
    record = db.get(FileRecord, file_id)
    if record is None:
        raise NotFoundError("File not found.")
    # Prepare transformer for visualization if source CRS is projected
    transformer_for_display = None
    try:
        if record.source_crs and record.source_crs_kind == "projected":
            src_crs = CRS.from_user_input(record.source_crs)
            dst_crs = CRS.from_epsg(4326)
            transformer_for_display = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    except Exception:
        transformer_for_display = None
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 50
    offset = (page - 1) * page_size
    base_q = db.query(FeatureMeasurement).filter(FeatureMeasurement.file_id == file_id).order_by(FeatureMeasurement.feature_index)
    total_items = base_q.count()
    features_all = base_q.all()
    summary = _build_summary(record, features_all)
    features_page = features_all[offset:offset + page_size]
    items = []
    for f in features_page:
        area_obj = None
        length_obj = None
        if f.measurement_type == MeasurementType.AREA.value and f.area_square_meters is not None:
            area_obj = AreaMeasurement.from_square_meters(float(f.area_square_meters))
        if f.measurement_type == MeasurementType.LENGTH.value and f.length_meters is not None:
            length_obj = LengthMeasurement.from_meters(float(f.length_meters))
        error_obj = None
        if f.error_code:
            from app.schemas.measurements import FeatureError
            error_obj = FeatureError(code=f.error_code, message=f.error_message or "")
        geometry_for_display = f.geometry
        if transformer_for_display and f.geometry:
            try:
                geom = shape(f.geometry)
                # Simple transform using pyproj; shapely 2.x supports transform with a callable
                from shapely.ops import transform as shapely_transform
                def _project(x, y, z=None):
                    return transformer_for_display.transform(x, y)
                geom_t = shapely_transform(_project, geom)
                geometry_for_display = mapping(geom_t)
            except Exception:
                geometry_for_display = f.geometry
        out = FeatureMeasurementOut(
            feature_index=f.feature_index,
            feature_id=f.feature_id,
            geometry_type=f.geometry_type,
            measurement_status=f.measurement_status,
            measurement_type=f.measurement_type,
            area=area_obj,
            length=length_obj,
            error=error_obj,
            properties=f.properties,
            geometry=geometry_for_display,
        )
        items.append(out)

    pagination = PaginationMeta.build(page=page, page_size=page_size, total_items=total_items)
    return FileMeasurementsResponse(
        file=FileDetail.model_validate(record),
        summary=summary,
        features=items,
        pagination=pagination,
    )


def _build_summary(record: FileRecord, features: list[FeatureMeasurement]) -> MeasurementSummary:
    counts: dict[str, int] = {}
    measured = not_applicable = failed = not_measurable = 0
    total_area = 0.0
    total_length = 0.0
    area_features = 0
    length_features = 0
    for feature in features:
        gt = feature.geometry_type
        counts[gt] = counts.get(gt, 0) + 1
        if feature.measurement_status == MeasurementStatus.MEASURED.value:
            measured += 1
            if feature.measurement_type == MeasurementType.AREA.value:
                total_area += feature.area_square_meters or 0.0
                area_features += 1
            elif feature.measurement_type == MeasurementType.LENGTH.value:
                total_length += feature.length_meters or 0.0
                length_features += 1
        elif feature.measurement_status == MeasurementStatus.NOT_APPLICABLE.value:
            not_applicable += 1
        elif feature.measurement_status == MeasurementStatus.FAILED.value:
            failed += 1
        elif feature.measurement_status == MeasurementStatus.NOT_MEASURABLE.value:
            not_measurable += 1

    return MeasurementSummary(
        total_features=len(features),
        geometry_type_counts=counts,
        measured_features=measured,
        not_applicable_features=not_applicable,
        not_measurable_features=not_measurable,
        failed_features=failed,
        area_features=area_features,
        length_features=length_features,
        total_area_square_meters=total_area,
        total_length_meters=total_length,
    )
