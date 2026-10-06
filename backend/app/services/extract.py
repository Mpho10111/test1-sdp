"""Per-commit history extraction.

Pipeline:
1. Resolve reference commit h_r:  git rev-parse <requested ref | HEAD>, where the
   requested ref may be a branch, tag or commit hash (stored on repos.reference_commit)
2. Commit metadata (one git log pass, oldest first):
     git [-c mailmap.blob=<h_r>:.mailmap] log --no-merges --reverse \
         --format="%H%x00%ct%x00%P%x00%an%x00%ae%x00%aN%x00%aE" <h_r>
   %aN/%aE are mailmap-aware; raw %an/%ae are kept on the commit row.
3. Per-commit diffs (single batched process, hashes fed on stdin):
     git diff-tree -r --root --numstat -z -M50% --stdin
   Observed output format (git 2.43, verified against a synthetic fixture):
     <hash>\\0                       NUL-terminated 40-hex commit hash
     <add>\\t<del>\\t<path>\\0        normal record
     <add>\\t<del>\\t\\0<old>\\0<new>\\0   rename/copy record
     -\\t-\\t...                     binary file (skipped entirely)
   Commits with no changes (empty commits) produce NO output at all, so the
   parser is driven by the hash delimiters and commits from step 2 are stored
   unconditionally.
   - pure renames contribute 0/0 and only set old_path
   - rename+edit counts only the edit, attributed to the new path
   - deletions keep the deleted path with removed > 0
   - plain 0/0 records without a rename (e.g. mode-only changes) are dropped
4. Indexes: `paths` (H[F] = changed paths + old paths + full tree of h_r) and
   `dirs_of_path` (H[D] = every ancestor directory of every known path).
"""

import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Iterator

from ..db import get_conn

_UTF8 = "utf-8"
_ERRORS = "replace"

_INSERT_CHANGE = """
INSERT INTO changes (repo_id, commit_id, path, old_path, added, removed)
VALUES (?, ?, ?, ?, ?, ?)
"""

# (path, old_path | None, added, removed)
FileChange = tuple[str, "str | None", int, int]


def extract_history(
    repo_id: int,
    repo_path: Path,
    ref: str | None = None,
    on_progress: Callable[..., None] = lambda _fraction: None,
) -> None:
    repo_path = Path(repo_path)
    reference = _resolve_reference(repo_path, ref)
    commits = _list_commits(repo_path, reference)
    if not commits:
        raise RuntimeError("No non-merge commits reachable from the reference commit")
    total = len(commits)

    conn = get_conn()
    try:
        for table in ("changes", "paths", "dirs_of_path", "commits", "authors"):
            conn.execute(f"DELETE FROM {table} WHERE repo_id = ?", (repo_id,))
        conn.execute("UPDATE repos SET reference_commit = ? WHERE id = ?", (reference, repo_id))
        conn.commit()

        def report(fraction: float) -> None:
            on_progress(0.05 + 0.85 * fraction)

        report(0.0)

        # --- step 2: store authors + commits ---
        author_ids: dict[tuple[str, str], int] = {}
        commit_ids: dict[str, int] = {}
        for c in commits:
            key = (c["aN"], c["aE"])
            author_id = author_ids.get(key)
            if author_id is None:
                conn.execute(
                    "INSERT OR IGNORE INTO authors (repo_id, name, email) VALUES (?, ?, ?)",
                    (repo_id, c["aN"], c["aE"]),
                )
                row = conn.execute(
                    "SELECT id FROM authors WHERE repo_id = ? AND name = ? AND email = ?",
                    (repo_id, c["aN"], c["aE"]),
                ).fetchone()
                author_id = int(row["id"])
                author_ids[key] = author_id
            cur = conn.execute(
                """INSERT INTO commits
                   (repo_id, hash, parent_hash, raw_name, raw_email, author_id, committer_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (repo_id, c["hash"], c["parent"], c["an"], c["ae"], author_id, c["ct"]),
            )
            commit_ids[c["hash"]] = int(cur.lastrowid)
        conn.commit()
        report(0.05)

        # --- step 3: stream per-commit diffs ---
        buffer: list[tuple] = []
        seen = 0
        step = max(1, total // 200)
        with tempfile.TemporaryFile("w+", encoding=_UTF8) as feed:
            for c in commits:
                feed.write(c["hash"] + "\n")
            feed.seek(0)
            proc = subprocess.Popen(
                [
                    "git", "-C", str(repo_path),
                    "diff-tree", "-r", "--root", "--numstat", "-z", "-M50%", "--stdin",
                ],
                stdin=feed,
                stdout=subprocess.PIPE,
            )
            assert proc.stdout is not None
            try:
                for commit_hash, files in _parse_diff_stream(proc.stdout):
                    commit_id = commit_ids.get(commit_hash)
                    if commit_id is None:
                        continue
                    for path, old_path, added, removed in files:
                        buffer.append((repo_id, commit_id, path, old_path, added, removed))
                    seen += 1
                    if len(buffer) >= 5000:
                        conn.executemany(_INSERT_CHANGE, buffer)
                        buffer.clear()
                        conn.commit()
                    if seen % step == 0:
                        report(0.05 + 0.60 * seen / total)
            finally:
                proc.stdout.close()
                proc.wait()
            if proc.returncode != 0:
                raise RuntimeError("git diff-tree failed while reading history")
        if buffer:
            conn.executemany(_INSERT_CHANGE, buffer)
            buffer.clear()
        conn.commit()
        report(0.70)

        # --- step 4: paths and dirs_of_path indexes ---
        known: set[str] = set()
        rows = conn.execute(
            """SELECT path FROM changes WHERE repo_id = ?
               UNION SELECT old_path FROM changes WHERE repo_id = ? AND old_path IS NOT NULL""",
            (repo_id, repo_id),
        ).fetchall()
        for row in rows:
            known.add(row[0])
        for entry in _ls_tree(repo_path, reference):
            known.add(entry)
        known.discard("")

        if known:
            conn.executemany(
                "INSERT OR IGNORE INTO paths (repo_id, path) VALUES (?, ?)",
                ((repo_id, p) for p in known),
            )
        dir_rows: set[tuple[int, str, str]] = set()
        for path in known:
            parts = path.split("/")
            for i in range(1, len(parts)):
                dir_rows.add((repo_id, path, "/".join(parts[:i])))
        if dir_rows:
            conn.executemany(
                "INSERT OR IGNORE INTO dirs_of_path (repo_id, path, dir) VALUES (?, ?, ?)",
                dir_rows,
            )
        conn.commit()
        report(1.0)
    finally:
        conn.close()


def _parse_diff_stream(stream) -> Iterator[tuple[str, list[FileChange]]]:
    """Parse `git diff-tree --stdin --numstat -z` output (format in module docstring)."""
    buf = b""
    files: list[FileChange] = []
    current: str | None = None
    # rename state: 0 = normal, 1 = awaiting old path, 2 = awaiting new path
    state = 0
    rename_old = ""
    rename_counts = (0, 0)
    rename_binary = False

    def segments() -> Iterator[bytes]:
        nonlocal buf
        while True:
            chunk = stream.read(1 << 16)
            if not chunk:
                break
            buf += chunk
            while True:
                i = buf.find(b"\x00")
                if i < 0:
                    break
                yield buf[:i]
                buf = buf[i + 1:]
        if buf:
            yield buf

    for seg in segments():
        if state == 1:  # old path of a rename (consumed raw, never a hash)
            rename_old = seg.decode(_UTF8, _ERRORS)
            state = 2
            continue
        if state == 2:  # new path of a rename
            new_path = seg.decode(_UTF8, _ERRORS)
            state = 0
            if not rename_binary:
                # pure renames are kept (0/0 with old_path set); binary renames skipped
                files.append((new_path, rename_old, rename_counts[0], rename_counts[1]))
            continue

        if len(seg) == 40 and b"\t" not in seg:
            if current is not None:
                yield current, files
            current = seg.decode(_UTF8, _ERRORS)
            files = []
            continue

        if b"\t" not in seg:
            continue  # stray segment, ignore

        i1 = seg.find(b"\t")
        i2 = seg.find(b"\t", i1 + 1)
        if i2 < 0:
            continue
        added_field = seg[:i1]
        removed_field = seg[i1 + 1:i2]
        path_field = seg[i2 + 1:]

        binary = added_field == b"-" or removed_field == b"-"
        if binary:
            added = removed = 0
        else:
            try:
                added = int(added_field)
                removed = int(removed_field)
            except ValueError:
                continue

        if path_field == b"":
            rename_old = ""
            rename_counts = (added, removed)
            rename_binary = binary
            state = 1
            continue

        if binary:
            continue
        path = path_field.decode(_UTF8, _ERRORS)
        if added == 0 and removed == 0:
            continue  # mode-only change etc.
        files.append((path, None, added, removed))

    if current is not None:
        yield current, files


def _resolve_reference(repo_path: Path, ref: str | None) -> str:
    """Resolve h_r: an explicit branch, tag or commit hash — otherwise HEAD.

    Mirror clones are bare, so a short name such as `main` or `v1.7` is also
    tried in the refs/heads and refs/tags namespaces.
    """
    spec = (ref or "").strip() or "HEAD"
    for candidate in (spec, f"refs/heads/{spec}", f"refs/tags/{spec}"):
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--verify", "--quiet",
             f"{candidate}^{{commit}}"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    raise RuntimeError(f"Could not resolve reference {spec!r} in this repository")


def _list_commits(repo_path: Path, reference: str) -> list[dict]:
    args = ["git", "-C", str(repo_path)]
    if _has_mailmap(repo_path, reference):
        args += ["-c", f"mailmap.blob={reference}:.mailmap"]
    args += [
        "log", "--no-merges", "--reverse",
        "--format=%H%x00%ct%x00%P%x00%an%x00%ae%x00%aN%x00%aE",
        reference,
    ]
    out = subprocess.run(args, capture_output=True, check=True).stdout
    commits: list[dict] = []
    for line in out.split(b"\n"):
        if not line:
            continue
        fields = line.split(b"\x00")
        if len(fields) != 7:
            raise RuntimeError(f"Unexpected git log output line: {line[:120]!r}")
        h, ct, parents, an, ae, a_n, a_e = (f.decode(_UTF8, _ERRORS) for f in fields)
        commits.append(
            {
                "hash": h,
                "ct": int(ct),
                "parent": parents.split(" ")[0] if parents else None,
                "an": an,
                "ae": ae,
                "aN": a_n,
                "aE": a_e,
            }
        )
    return commits


def _has_mailmap(repo_path: Path, reference: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo_path), "cat-file", "-e", f"{reference}:.mailmap"],
        capture_output=True,
    )
    return proc.returncode == 0


def _ls_tree(repo_path: Path, reference: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo_path), "ls-tree", "-r", "-z", reference],
        capture_output=True,
        check=True,
    ).stdout
    paths: list[str] = []
    for entry in out.split(b"\x00"):
        if not entry:
            continue
        meta, _, raw_path = entry.partition(b"\t")
        mode = meta.split(b" ", 1)[0]
        if mode == b"160000":
            continue  # submodule gitlink, not a file
        paths.append(raw_path.decode(_UTF8, _ERRORS))
    return paths


def _git(repo_path: Path, args: list[str]) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        check=True,
    )
    return proc.stdout.decode(_UTF8, _ERRORS)
