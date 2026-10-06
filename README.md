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

## Metrics

For a repository with reference commit `h_r`, every object (file, directory,
repository) is measured over a commit set `H`:

| Metric | Meaning |
| --- | --- |
| `l+` / `l-` | lines added / removed |
| `δ = l+ − l-` | growth |
| `λ = l+ + l-` | churn |
| `n` | modifying commits — commits in `H` with `λ > 0` on the object |
| `η = n / |H|` | frequency |
| `ρ = λ / |H|` | churn rate |
| `ω` | author ownership — that author's share of the object's churn |

Directory metrics are recursive subtree sums and the repository is the root
directory. Merge commits are excluded, binary files are excluded, and renames
are detected at 50% similarity: a pure rename contributes nothing, a rename
with edits counts only the edit and is attributed to the new path. Deletions
keep their removed lines at the deleted path.

## Choosing the reference commit `h_r`

Both ingest methods accept an optional **Reference commit / branch / tag**;
leave it empty for `HEAD`. The commit walk, the `.mailmap` used for author
canonicalisation and the file list are all resolved from that commit, so
metrics can be reproduced exactly as of any point in history. An unresolvable
ref fails the ingest with a clear error rather than silently falling back.

## Filtering

The filter bar narrows `H` for every view at once:

- **From / To** — half-open committer-date window `[from, to)`
- **Author** — a single canonical identity
- **Commit list** — explicit hashes, comma separated

The pill `H = n of m commits` always shows the active set size.

## Author merging

Identities are canonicalised automatically from the repository's `.mailmap` at
`h_r`. Anything the mailmap misses can be merged manually in the **Authors**
tab: tick the identities to absorb, pick the survivor with ◎, then press
**Merge selected into target**. Merges are recorded in `author_aliases`.

## Scale & performance

History is extracted once, in a single batched `git diff-tree --stdin` stream,
into precomputed fact tables (`changes`, `paths`, `dirs_of_path`); every metric
is then an indexed SQL aggregate, so no repository is ever re-walked per query.
Measured with one uvicorn worker on SQLite (git 2.43):

| Repository | Non-merge commits | `/metrics/overview` |
| --- | --- | --- |
| cJSON | 955 | 47 ms |
| redis | 11,874 | 138 ms |
| git | 61,101 | 370 ms |

Ingesting the 61k-commit git repository took 121 s end to end — a 606 MB
mirror clone plus ~30 s of extraction into 136,706 change rows — and its totals
match raw `git log --no-merges -M50% --numstat` exactly (4,070,371 added /
2,375,604 removed / 6,445,975 churn). Other endpoints on that repository:
files page 98 ms, directories page 80 ms, single-object detail ~210 ms,
filtered overview (four-year window, `H` = 11,555) 180 ms, author list
(2,498 authors) 33 ms, commit page 5 ms. The whole database is ~36 MB with
every repository ingested.

## Status

- [x] Ingestion: zip upload and full remote clone, background jobs with progress
- [x] History extraction: rename / binary / deletion rules, mailmap-aware authors
- [x] Configurable reference commit `h_r`
- [x] Metric engine: file, directory, repository, commit set, author ownership
- [x] Filtering: time window, author, explicit commit list
- [x] Manual author merging
- [x] Multi-repo dashboard: charts, drill-down, sorting, search, pagination
- [x] Verified at scale on a 61k-commit repository
- [x] Commit browser / picker UI
- [x] Richer visualisations (directory treemap, ownership donut, contribution heatmap, monthly activity)
