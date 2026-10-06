from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import db

router = APIRouter(prefix="/api/repos/{repo_id}/authors", tags=["authors"])


@router.get("")
def list_authors(repo_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """SELECT a.id, a.name, a.email,
                      COUNT(c.id) AS commit_count,
                      MIN(c.committer_date) AS first_commit,
                      MAX(c.committer_date) AS last_commit
               FROM authors a
               LEFT JOIN commits c ON c.author_id = a.id AND c.repo_id = a.repo_id
               WHERE a.repo_id = ?
               GROUP BY a.id
               ORDER BY commit_count DESC, a.name""",
            (repo_id,),
        ).fetchall()
    return [dict(row) for row in rows]


class MergeRequest(BaseModel):
    target_author_id: int
    source_author_ids: list[int]


@router.post("/merge")
def merge_authors(repo_id: int, body: MergeRequest) -> dict:
    source_ids = sorted({i for i in body.source_author_ids if i != body.target_author_id})
    if not source_ids:
        raise HTTPException(status_code=400, detail="Nothing to merge")

    ids = [body.target_author_id, *source_ids]
    placeholders = ",".join("?" for _ in ids)
    with db() as conn:
        rows = conn.execute(
            f"SELECT id, name, email FROM authors WHERE repo_id = ? AND id IN ({placeholders})",
            [repo_id, *ids],
        ).fetchall()
        found = {row["id"] for row in rows}
        missing = [i for i in ids if i not in found]
        if missing:
            raise HTTPException(status_code=404, detail=f"Unknown author ids: {missing}")

        for row in rows:
            if row["id"] in source_ids:
                conn.execute(
                    """INSERT OR IGNORE INTO author_aliases (repo_id, raw_name, raw_email, author_id)
                       VALUES (?, ?, ?, ?)""",
                    (repo_id, row["name"], row["email"], body.target_author_id),
                )
        src_ph = ",".join("?" for _ in source_ids)
        conn.execute(
            f"UPDATE commits SET author_id = ? WHERE repo_id = ? AND author_id IN ({src_ph})",
            [body.target_author_id, repo_id, *source_ids],
        )
        conn.execute(
            f"DELETE FROM authors WHERE repo_id = ? AND id IN ({src_ph})",
            [repo_id, *source_ids],
        )
    return {"merged": len(source_ids), "target_author_id": body.target_author_id}
