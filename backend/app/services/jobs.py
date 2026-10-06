import shutil
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from ..config import UPLOADS_DIR
from ..db import db
from .ingest import ingest_repo


def register_repo(source: str, source_uri: str, name: str | None, ref: str | None = None) -> int:
    if not name:
        derived = Path(source_uri.rstrip("/")).name
        name = derived[:-4] if derived.endswith(".git") else derived
    name = _unique_name(name or "repo")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO repos (name, source, source_uri, requested_ref, status)"
            " VALUES (?, ?, ?, ?, 'created')",
            (name, source, source_uri, (ref or "").strip() or None),
        )
        return int(cur.lastrowid)


def save_zip_upload(repo_id: int, file: UploadFile) -> None:
    dest = UPLOADS_DIR / f"{repo_id}.zip"
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)


def set_status(
    repo_id: int,
    status: str | None = None,
    progress: float | None = None,
    error: str | None = None,
    repo_path: str | None = None,
    reference_commit: str | None = None,
) -> None:
    updates: list[str] = []
    params: list[Any] = []
    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if progress is not None:
        updates.append("progress = ?")
        params.append(progress)
    if error is not None:
        updates.append("error = ?")
        params.append(error)
    if repo_path is not None:
        updates.append("repo_path = ?")
        params.append(repo_path)
    if reference_commit is not None:
        updates.append("reference_commit = ?")
        params.append(reference_commit)
    if not updates:
        return
    params.append(repo_id)
    with db() as conn:
        conn.execute(f"UPDATE repos SET {', '.join(updates)} WHERE id = ?", params)


def run_ingest(repo_id: int) -> None:
    try:
        set_status(repo_id, status="ingesting", progress=0.0, error="")
        ingest_repo(repo_id, on_progress=lambda progress: set_status(repo_id, progress=progress))
        set_status(repo_id, status="ready", progress=1.0)
    except Exception as exc:  # keep the failure visible in the UI
        set_status(repo_id, status="error", error=str(exc) or exc.__class__.__name__)


def _unique_name(base: str) -> str:
    with db() as conn:
        taken = {row["name"] for row in conn.execute("SELECT name FROM repos")}
    if base not in taken:
        return base
    index = 2
    while f"{base}-{index}" in taken:
        index += 1
    return f"{base}-{index}"
