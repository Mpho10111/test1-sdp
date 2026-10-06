from fastapi import APIRouter, HTTPException

from ..db import db

router = APIRouter(prefix="/api/repos/{repo_id}/commits", tags=["commits"])


@router.get("")
def list_commits(
    repo_id: int,
    author_id: int | None = None,
    from_ts: int | None = None,
    to_ts: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    where = ["c.repo_id = ?"]
    params: list = [repo_id]
    if author_id is not None:
        where.append("c.author_id = ?")
        params.append(author_id)
    if from_ts is not None:
        where.append("c.committer_date >= ?")
        params.append(from_ts)
    if to_ts is not None:
        where.append("c.committer_date < ?")
        params.append(to_ts)
    clause = " AND ".join(where)

    with db() as conn:
        if conn.execute("SELECT 1 FROM repos WHERE id = ?", (repo_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Repository not found")
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM commits c WHERE {clause}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"""SELECT c.hash, c.parent_hash, c.author_id, c.committer_date,
                       a.name AS author_name, a.email AS author_email
                FROM commits c
                JOIN authors a ON a.id = c.author_id
                WHERE {clause}
                ORDER BY c.committer_date DESC
                LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
    return {"total": total, "items": [dict(row) for row in rows]}
