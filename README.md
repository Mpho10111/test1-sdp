# RAT — Repo Analysis Tool

A web dashboard that analyzes Git repositories and measures evolution metrics
per author, file, directory, repository, and commit set.

## Stack

- Backend: Python + FastAPI + SQLite (`backend/`)
- Frontend: React + TypeScript + Vite + ECharts (`frontend/`)

## Layout

    backend/app/
      main.py        FastAPI entry point
      config.py      data paths / settings
      db.py          SQLite connection + schema
      routers/       HTTP endpoints (repos, commits, authors, metrics)
      services/      ingest, history extraction, metric engine, jobs
    frontend/src/    React dashboard
    data/            runtime data: uploads, cloned repos, rat.db (gitignored)

## Running (development)

Backend:

    cd backend
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn app.main:app --reload --port 8000

Frontend (second terminal):

    cd frontend
    npm install
    npm run dev

Open http://localhost:5173 — it proxies /api to the backend.

## Status

- [x] Skeleton, database schema, repo management API
- [ ] History extraction (per-commit facts) — next
- [ ] Metric engine (file/dir/repo/commit-set/author)
- [ ] Dashboard filters and charts
- [ ] Author merging UI, multi-repo views
