import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Callable

from ..config import REPOS_DIR, UPLOADS_DIR
from ..db import db
from .extract import extract_history

ProgressCallback = Callable[..., None]


class IngestError(Exception):
    pass


def ingest_repo(repo_id: int, on_progress: ProgressCallback) -> None:
    with db() as conn:
        row = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
    if row is None:
        raise IngestError(f"repo {repo_id} not found")

    dest = REPOS_DIR / str(repo_id)
    if dest.exists():
        shutil.rmtree(dest)

    if row["source"] == "zip":
        repo_path = _extract_zip(UPLOADS_DIR / f"{repo_id}.zip", dest)
    elif row["source"] == "url":
        repo_path = _clone_mirror(row["source_uri"], dest)
    else:
        raise IngestError(f"Unknown source type: {row['source']}")

    _assert_git_repo(repo_path)
    with db() as conn:
        conn.execute("UPDATE repos SET repo_path = ? WHERE id = ?", (str(repo_path), repo_id))

    extract_history(repo_id, repo_path, ref=row["requested_ref"], on_progress=on_progress)


def _extract_zip(zip_path: Path, dest: Path) -> Path:
    if not zip_path.is_file():
        raise IngestError("Uploaded zip file is missing")
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                target = (dest / member.filename).resolve()
                if not target.is_relative_to(dest.resolve()):
                    raise IngestError("Zip contains unsafe paths (path traversal)")
            archive.extractall(dest)
    except zipfile.BadZipFile as exc:
        raise IngestError("Uploaded file is not a valid zip archive") from exc
    return _find_repo_root(dest)


def _find_repo_root(base: Path) -> Path:
    git_markers = [p.parent for p in base.rglob(".git")]
    if not git_markers:
        raise IngestError("No .git found in the uploaded zip — include the .git file or directory")
    return min(git_markers, key=lambda p: len(p.parts))


def _clone_mirror(url: str, dest: Path) -> Path:
    proc = subprocess.run(
        ["git", "clone", "--mirror", url, str(dest)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise IngestError(f"git clone failed: {proc.stderr.strip()[:500]}")
    return dest


def _assert_git_repo(path: Path) -> None:
    proc = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise IngestError("Not a valid git repository (could not open .git)")
