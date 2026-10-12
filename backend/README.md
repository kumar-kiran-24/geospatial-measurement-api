# Geospatial Measurement API Backend

FastAPI backend for measuring area and length of geospatial features uploaded as KML or zipped Shapefiles.

## Features
- Upload KML or ZIP containing Shapefile
- Automatic CRS detection and measurement strategy selection
- Planar or geodesic measurement with pyproj
- PostgreSQL persistence via SQLAlchemy + Alembic
- Paginated file history and measurements endpoints

## Local Development

1. Copy environment:
```bash
cp backend/.env.example backend/.env
# edit backend/.env with your PostgreSQL credentials
```

2. Install dependencies:
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
```

3. Run migrations (requires DB accessible):
```bash
alembic upgrade head
```

4. Start API:
```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Swagger UI at http://127.0.0.1:8000/docs

## API Endpoints
- `POST /api/files/` upload
- `GET /api/files/` list
- `GET /api/files/{file_id}/` detail
- `DELETE /api/files/{file_id}/` delete
- `GET /api/files/{file_id}/measurements/` measurements
- `GET /health` liveness
- `GET /health/readiness` readiness

## Tests
```bash
pytest -q
```

## Limitations

PostgreSQL authentication is currently unresolved. The backend can be imported and unit tested with SQLite in-memory, but file upload, persistence and readiness checks require a working PostgreSQL connection. See root README for details.

## Docker
```bash
docker compose up --build
```
