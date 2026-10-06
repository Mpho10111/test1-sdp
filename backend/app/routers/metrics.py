from fastapi import APIRouter, HTTPException

from ..db import db
from ..services import metrics as M

router = APIRouter(prefix="/api/repos/{repo_id}/metrics", tags=["metrics"])

_KINDS = ("file", "dir", "repo")


def _require_ready(repo_id: int) -> None:
    with db() as conn:
        row = conn.execute("SELECT status FROM repos WHERE id = ?", (repo_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    if row["status"] != "ready":
        raise HTTPException(
            status_code=409, detail=f"Repository is not ready yet (status: {row['status']})"
        )


def _parse_commits(commits: str | None) -> list[str] | None:
    return None if commits is None else [h.strip() for h in commits.split(",") if h.strip()]


def _filter(from_ts: int | None, to_ts: int | None, commits: str | None,
            author_id: int | None) -> M.CommitFilter:
    return M.CommitFilter(
        from_ts=from_ts, to_ts=to_ts, hashes=_parse_commits(commits), author_id=author_id
    )


@router.get("/overview")
def overview(
    repo_id: int,
    from_ts: int | None = None,
    to_ts: int | None = None,
    commits: str | None = None,
    author_id: int | None = None,
):
    _require_ready(repo_id)
    return M.overview(repo_id, _filter(from_ts, to_ts, commits, author_id))


@router.get("/objects")
def objects(
    repo_id: int,
    kind: str = "file",
    q: str | None = None,
    dir: str | None = None,
    sort: str = "churn",
    order: str = "desc",
    limit: int = 100,
    offset: int = 0,
    from_ts: int | None = None,
    to_ts: int | None = None,
    commits: str | None = None,
    author_id: int | None = None,
):
    _require_ready(repo_id)
    if kind not in ("file", "dir"):
        raise HTTPException(status_code=422, detail="kind must be 'file' or 'dir'")
    return M.objects_list(
        repo_id,
        kind,
        _filter(from_ts, to_ts, commits, author_id),
        q=q,
        dir_prefix=dir,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )


@router.get("/object")
def object_detail(
    repo_id: int,
    path: str = "",
    kind: str | None = None,
    from_ts: int | None = None,
    to_ts: int | None = None,
    commits: str | None = None,
    author_id: int | None = None,
):
    _require_ready(repo_id)
    resolved = kind or ("repo" if path == "" else ("dir" if path.endswith("/") else "file"))
    if resolved not in _KINDS:
        raise HTTPException(status_code=422, detail="kind must be file, dir or repo")
    if resolved == "file" and path.endswith("/"):
        raise HTTPException(status_code=422, detail="File paths must not end with '/'")
    clean = path.rstrip("/") if resolved in ("dir", "repo") else path
    cf = _filter(from_ts, to_ts, commits, author_id)
    metrics = M.object_metrics(repo_id, resolved, clean, cf)
    return {
        "kind": resolved,
        "path": clean,
        "commit_set": {
            "size": metrics.pop("commit_set_size"),
            "hashes": len(cf.hashes) if cf.hashes is not None else None,
            "from_ts": cf.from_ts,
            "to_ts": cf.to_ts,
            "author_id": cf.author_id,
        },
        "metrics": metrics,
        "authors": M.by_author(repo_id, resolved, clean, cf),
        "timeline": M.object_timeline(repo_id, resolved, clean, cf),
    }
