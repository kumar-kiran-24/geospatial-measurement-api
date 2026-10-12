"""initial schema

Revision ID: 001_initial
Revises: 
Create Date: 2025-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # uploaded_files
    op.create_table(
        'uploaded_files',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('original_filename', sa.String(length=512), nullable=False),
        sa.Column('storage_path', sa.Text(), nullable=False),
        sa.Column('file_format', sa.String(length=16), nullable=False),
        sa.Column('content_type', sa.String(length=128), nullable=True),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('checksum_sha256', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='PENDING'),
        sa.Column('error_code', sa.String(length=64), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('processing_started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('processing_completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('feature_count', sa.Integer(), nullable=True),
        sa.Column('source_crs', sa.Text(), nullable=True),
        sa.Column('source_crs_name', sa.String(length=255), nullable=True),
        sa.Column('source_crs_kind', sa.String(length=16), nullable=True),
        sa.Column('measurement_strategy', sa.String(length=32), nullable=True),
        sa.Column('measurement_crs', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("status IN ('PENDING','PROCESSING','COMPLETED','FAILED')", name='status_valid'),
        sa.CheckConstraint("file_format IN ('kml','shapefile')", name='format_valid'),
        sa.CheckConstraint("source_crs_kind IS NULL OR source_crs_kind IN ('geographic','projected','unknown')", name='crs_kind_valid'),
        sa.CheckConstraint("measurement_strategy IS NULL OR measurement_strategy IN ('PROJECTED_SOURCE','LOCAL_EQUAL_AREA','LOCAL_UTM','GEODESIC','NONE')", name='strategy_valid'),
        sa.CheckConstraint("size_bytes >= 0", name='size_non_negative'),
        sa.CheckConstraint("feature_count IS NULL OR feature_count >= 0", name='feature_count_valid')
    )
    op.create_index('ix_uploaded_files_created_at', 'uploaded_files', ['created_at'])
    op.create_index('ix_uploaded_files_status', 'uploaded_files', ['status'])
    op.create_index('ix_uploaded_files_checksum_sha256', 'uploaded_files', ['checksum_sha256'])

    # feature_measurements
    op.create_table(
        'feature_measurements',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('file_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('feature_index', sa.Integer(), nullable=False),
        sa.Column('feature_id', sa.String(length=255), nullable=True),
        sa.Column('geometry_type', sa.String(length=32), nullable=False),
        sa.Column('geometry', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('properties', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('measurement_status', sa.String(length=24), nullable=False),
        sa.Column('measurement_type', sa.String(length=16), nullable=False, server_default='none'),
        sa.Column('area_square_meters', sa.Float(), nullable=True),
        sa.Column('length_meters', sa.Float(), nullable=True),
        sa.Column('error_code', sa.String(length=64), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['file_id'], ['uploaded_files.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('file_id', 'feature_index', name='feature_index_unique'),
        sa.CheckConstraint("measurement_status IN ('MEASURED','NOT_APPLICABLE','NOT_MEASURABLE','FAILED')", name='status_valid'),
        sa.CheckConstraint("measurement_type IN ('area','length','none')", name='type_valid'),
        sa.CheckConstraint("feature_index >= 0", name='feature_index_non_negative'),
        sa.CheckConstraint("area_square_meters IS NULL OR area_square_meters >= 0", name='area_non_negative'),
        sa.CheckConstraint("length_meters IS NULL OR length_meters >= 0", name='length_non_negative')
    )
    op.create_index('ix_feature_measurements_file_id_feature_index', 'feature_measurements', ['file_id', 'feature_index'])
    op.create_index('ix_feature_measurements_file_id_status', 'feature_measurements', ['file_id', 'measurement_status'])
    op.create_index('ix_feature_measurements_file_id_geometry_type', 'feature_measurements', ['file_id', 'geometry_type'])


def downgrade() -> None:
    op.drop_index('ix_feature_measurements_file_id_geometry_type', table_name='feature_measurements')
    op.drop_index('ix_feature_measurements_file_id_status', table_name='feature_measurements')
    op.drop_index('ix_feature_measurements_file_id_feature_index', table_name='feature_measurements')
    op.drop_table('feature_measurements')
    op.drop_index('ix_uploaded_files_checksum_sha256', table_name='uploaded_files')
    op.drop_index('ix_uploaded_files_status', table_name='uploaded_files')
    op.drop_index('ix_uploaded_files_created_at', table_name='uploaded_files')
    op.drop_table('uploaded_files')
