import shutil

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..config import REPOS_DIR, UPLOADS_DIR
from ..db import db
from ..services import jobs

router = APIRouter(prefix="/api/repos", tags=["repos"])


class CloneRequest(BaseModel):
    url: str
    name: str | None = None
    ref: str | None = None  # branch, tag or commit hash; defaults to HEAD


@router.get("")
def list_repos() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """SELECT r.*,
                      (SELECT COUNT(*) FROM commits c WHERE c.repo_id = r.id) AS commit_count
               FROM repos r
               ORDER BY r.created_at DESC, r.id DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


@router.get("/{repo_id}")
def get_repo(repo_id: int) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return dict(row)


@router.post("/upload", status_code=202)
def upload_zip(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    name: str | None = Form(None),
    ref: str | None = Form(None),
) -> dict:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Upload a .zip file containing a repository (with .git)")
    repo_id = jobs.register_repo(source="zip", source_uri=file.filename, name=name, ref=ref)
    jobs.save_zip_upload(repo_id, file)
    background.add_task(jobs.run_ingest, repo_id)
    return {"repo_id": repo_id, "status": "ingesting"}


@router.post("/clone", status_code=202)
def clone_remote(background: BackgroundTasks, body: CloneRequest) -> dict:
    url = body.url.strip()
    if not url.startswith(("http://", "https://", "ssh://", "git@")):
        raise HTTPException(status_code=400, detail="Provide a valid clone URL (https://, ssh:// or git@)")
    repo_id = jobs.register_repo(source="url", source_uri=url, name=body.name, ref=body.ref)
    background.add_task(jobs.run_ingest, repo_id)
    return {"repo_id": repo_id, "status": "ingesting"}


@router.delete("/{repo_id}", status_code=204)
def delete_repo(repo_id: int) -> None:
    with db() as conn:
        row = conn.execute("SELECT id FROM repos WHERE id = ?", (repo_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Repository not found")
        conn.execute("DELETE FROM repos WHERE id = ?", (repo_id,))
    for path in (REPOS_DIR / str(repo_id), UPLOADS_DIR / f"{repo_id}.zip"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.is_file():
            path.unlink(missing_ok=True)
