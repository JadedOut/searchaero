# Seataero

Search United award flight availability with precision ledger data.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (for PostgreSQL)
- [Node.js](https://nodejs.org/) 18+
- Python 3.11+ (venv already set up in `scripts/experiments/.venv`)

## Quick Start

### 1. Start the database

```bash
docker compose up -d
```

This launches PostgreSQL 16 on `localhost:5432`.

### 2. Start the backend

```bash
scripts/experiments/.venv/Scripts/python.exe -m uvicorn web.api:app --reload --port 8000
```

API is now live at http://localhost:8000. Key endpoints:

- `GET /api/health` — health check
- `GET /api/stats` — database statistics
- `GET /api/search?origin=YYZ&destination=EWR` — search availability
- `GET /api/search/detail?origin=YYZ&destination=EWR&date=2026-05-01` — date detail

### 3. Start the frontend

In a separate terminal:

```bash
cd web/frontend
npm install   # first time only
npm run dev
```

Frontend is now live at http://localhost:3000.

## Running Tests

```bash
# Python API tests (mocked, no DB required)
scripts/experiments/.venv/Scripts/python.exe -m pytest tests/test_api.py -v

# All Python tests (requires PostgreSQL running)
scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v

# Frontend type check
cd web/frontend && npx tsc --noEmit

# Frontend production build
cd web/frontend && npm run build
```

## Project Structure

```
core/           Data models (models.py) and database layer (db.py)
web/
  api.py        FastAPI backend
  frontend/     Next.js frontend
routes/         Test route files
scripts/        Scraper, burn-in testing, analysis tools
tests/          Python test suite
```
