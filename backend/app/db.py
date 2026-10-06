import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,                      -- 'zip' | 'url'
    source_uri TEXT,                           -- original filename or clone URL
    repo_path TEXT,                            -- local path of extracted/cloned repo
    reference_commit TEXT,                     -- resolved h_r hash (default: HEAD)
    status TEXT NOT NULL DEFAULT 'created',    -- created | ingesting | ready | error
    progress REAL NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS authors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    UNIQUE (repo_id, name, email)
);

CREATE TABLE IF NOT EXISTS commits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    hash TEXT NOT NULL,
    parent_hash TEXT,
    raw_name TEXT NOT NULL,                    -- as recorded in the commit
    raw_email TEXT NOT NULL,
    author_id INTEGER NOT NULL REFERENCES authors(id),
    committer_date INTEGER NOT NULL,           -- unix timestamp
    UNIQUE (repo_id, hash)
);

-- manual author merges kept for traceability (queries use commits.author_id)
CREATE TABLE IF NOT EXISTS author_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    raw_name TEXT NOT NULL,
    raw_email TEXT NOT NULL,
    author_id INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    UNIQUE (repo_id, raw_name, raw_email)
);

-- per-commit, per-path line deltas; binaries excluded; deleted paths kept
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    commit_id INTEGER NOT NULL REFERENCES commits(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    old_path TEXT,                             -- set when the row results from a rename
    added INTEGER NOT NULL DEFAULT 0,
    removed INTEGER NOT NULL DEFAULT 0
);

-- every path that ever existed (H[F]) — the browsable object list
CREATE TABLE IF NOT EXISTS paths (
    repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    PRIMARY KEY (repo_id, path)
);

-- all ancestor directories of every known path (root '' included) — makes
-- directory metrics a subtree sum instead of a recursive walk
CREATE TABLE IF NOT EXISTS dirs_of_path (
    repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    dir TEXT NOT NULL,
    PRIMARY KEY (repo_id, path, dir)
);

CREATE INDEX IF NOT EXISTS idx_commits_repo_date ON commits(repo_id, committer_date);
CREATE INDEX IF NOT EXISTS idx_commits_repo_author ON commits(repo_id, author_id);
CREATE INDEX IF NOT EXISTS idx_changes_repo_commit ON changes(repo_id, commit_id);
CREATE INDEX IF NOT EXISTS idx_changes_repo_path ON changes(repo_id, path);
CREATE INDEX IF NOT EXISTS idx_dirs_of_path_dir ON dirs_of_path(repo_id, dir);
"""


def get_conn() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    ensure_dirs()
    conn = get_conn()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
