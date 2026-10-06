from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
REPOS_DIR = DATA_DIR / "repos"
UPLOADS_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "rat.db"


def ensure_dirs() -> None:
    for path in (DATA_DIR, REPOS_DIR, UPLOADS_DIR):
        path.mkdir(parents=True, exist_ok=True)
