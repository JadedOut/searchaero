# Seataero Project

## Python Environment
- Python venv path: `C:\Users\jiami\local_workspace\seataero\scripts\experiments\.venv`
- Python executable: `C:\Users\jiami\local_workspace\seataero\scripts\experiments\.venv\Scripts\python.exe`
- Always use this venv for running scripts and tests

## Running Tests
```bash
cd C:/Users/jiami/local_workspace/seataero
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v
```

## Project Structure
- `core/` — Data models (models.py) and database layer (db.py)
- `scripts/experiments/` — Hybrid scraper, cookie farm, United API utilities
- `scrape.py` — Main CLI entry point (single route)
- `scripts/burn_in.py` — Multi-route runner with JSONL logging (supports `--one-shot` for single-pass and `--burn-limit` for auto-exit on cookie burns)
- `scripts/orchestrate.py` — Parallel orchestrator: splits routes across N workers, monitors health via status files, kills burned-out workers
- `scripts/analyze_burn_in.py` — Burn-in log analysis and reporting
- `scripts/verify_data.py` — Data verification reporting
- `routes/canada_test.txt` — 15 Canada→US test routes
- `routes/canada_us_all.txt` — Full Canada→US route list for production runs
- `docker-compose.yml` — PostgreSQL 16 container

## Burn-In Testing
```bash
# Single worker, continuous mode (10 min example)
scripts/experiments/.venv/Scripts/python.exe scripts/burn_in.py \
  --routes-file routes/canada_test.txt --duration 10 --create-schema

# Single worker, one-shot mode (scrape all routes once, then exit)
scripts/experiments/.venv/Scripts/python.exe scripts/burn_in.py \
  --routes-file routes/canada_test.txt --one-shot --create-schema

# Orchestrated parallel run (3 workers, one-shot, auto-kill on 10 burns)
scripts/experiments/.venv/Scripts/python.exe scripts/orchestrate.py \
  --routes-file routes/canada_us_all.txt --workers 3 --headless --create-schema

# Analyze results
scripts/experiments/.venv/Scripts/python.exe scripts/analyze_burn_in.py logs/burn_in_*.jsonl
```

## Web UI
```bash
# Start backend (FastAPI on port 8000)
cd C:/Users/jiami/local_workspace/seataero
scripts/experiments/.venv/Scripts/python.exe -m uvicorn web.api:app --reload --port 8000

# Start frontend (Next.js on port 3000, in a separate terminal)
cd C:/Users/jiami/local_workspace/seataero/web/frontend
npm run dev
```
- Backend: http://localhost:8000 (API endpoints: /api/health, /api/search, /api/search/detail)
- Frontend: http://localhost:3000
- Requires PostgreSQL to be running

## Database
- PostgreSQL via Docker: `docker compose up -d`
- Connection: `postgresql://seataero:seataero_dev@localhost:5432/seataero`
