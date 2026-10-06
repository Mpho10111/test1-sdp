"""Commit-set metric engine (file / directory / repository / author).

Metrics for an object o (a file, a directory subtree, or the repository
root) are defined over a commit set H described by CommitFilter:

    l+(o) = SUM(changes.added)      l-(o) = SUM(changes.removed)
    growth         delta  = l+ - l-
    churn          lambda = l+ + l-
    modifications  n      = # commits in H with lambda > 0 on o
    frequency      eta    = n / |H|
    churn rate     rho    = lambda / |H|

Author variants gate the changes by commits.author_id; ownership is

    omega(o, a) = lambda(o, a) / lambda(o)

where the denominator is computed without the author gate so that it
stays meaningful inside an author-filtered view.

Directory metrics are subtree sums: `dirs_of_path` maps every path to all
of its ancestor directories, so a directory query is a single indexed
JOIN. The root directory is special-cased (no JOIN = all changes) and is
identical to the repository metrics.
"""

from dataclasses import dataclass

from ..db import db

MAX_LIMIT = 1000
_MODS = "COUNT(DISTINCT CASE WHEN ch.added + ch.removed > 0 THEN ch.commit_id END)"
_SORT_COLUMNS = ("path", "added", "removed", "churn", "modifications")


@dataclass
class CommitFilter:
    """H description: half-open time window [from_ts, to_ts) on the
    committer date, an explicit commit-hash list, and/or an author gate."""

    from_ts: int | None = None
    to_ts: int | None = None
    hashes: list[str] | None = None
    author_id: int | None = None

    def without_author(self) -> "CommitFilter":
        return CommitFilter(from_ts=self.from_ts, to_ts=self.to_ts, hashes=self.hashes)


# --------------------------------------------------------------------------
# SQL builders
# --------------------------------------------------------------------------

def _commit_filter_sql(cf: CommitFilter) -> tuple[str, list]:
    """WHERE fragment over commits (aliased `c`), always prefixed by AND/1=1."""
    clauses: list[str] = []
    params: list = []
    if cf.from_ts is not None:
        clauses.append("c.committer_date >= ?")
        params.append(cf.from_ts)
    if cf.to_ts is not None:
        clauses.append("c.committer_date < ?")
        params.append(cf.to_ts)
    if cf.author_id is not None:
        clauses.append("c.author_id = ?")
        params.append(cf.author_id)
    if cf.hashes is not None:
        if not cf.hashes:
            clauses.append("0 = 1")  # explicit empty set matches nothing
        else:
            placeholders = ",".join("?" * len(cf.hashes))
            clauses.append(f"c.hash IN ({placeholders})")
            params.extend(cf.hashes)
    return " AND ".join(clauses), params


def _object_parts(kind: str, path: str) -> tuple[str, str, list]:
    """Return (join_sql, condition_sql, params) that restrict `changes ch`
    to the object."""
    if kind == "file":
        return "", "ch.path = ?", [path]
    if kind == "dir" and path:
        return (
            "JOIN dirs_of_path d ON d.repo_id = ch.repo_id AND d.path = ch.path",
            "d.dir = ?",
            [path],
        )
    return "", "", []  # repo root / dir ''


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def commit_set_size(repo_id: int, cf: CommitFilter) -> int:
    cf_sql, params = _commit_filter_sql(cf)
    where = f"AND {cf_sql}" if cf_sql else ""
    with db() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM commits c WHERE c.repo_id = ? {where}",
            [repo_id, *params],
        ).fetchone()
    return int(row["n"])


def object_totals(repo_id: int, kind: str, path: str, cf: CommitFilter) -> dict:
    """Raw sums (added, removed, modifications) for an object over H."""
    join, cond, obj_params = _object_parts(kind, path)
    cf_sql, cf_params = _commit_filter_sql(cf)
    conds = [c for c in (cond, cf_sql) if c]
    where = f"AND {' AND '.join(conds)}" if conds else ""
    with db() as conn:
        row = conn.execute(
            f"""SELECT COALESCE(SUM(ch.added), 0)   AS added,
                       COALESCE(SUM(ch.removed), 0) AS removed,
                       {_MODS}                      AS modifications
                FROM changes ch
                JOIN commits c ON c.id = ch.commit_id
                {join}
                WHERE ch.repo_id = ? {where}""",
            [repo_id, *obj_params, *cf_params],
        ).fetchone()
    return {
        "added": int(row["added"]),
        "removed": int(row["removed"]),
        "modifications": int(row["modifications"]),
    }


def _derive(added: int, removed: int, modifications: int, commit_count: int) -> dict:
    churn = added + removed
    return {
        "added": added,
        "removed": removed,
        "growth": added - removed,
        "churn": churn,
        "modifications": modifications,
        "frequency": round(modifications / commit_count, 6) if commit_count else 0.0,
        "churn_rate": round(churn / commit_count, 6) if commit_count else 0.0,
    }


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def object_metrics(repo_id: int, kind: str, path: str, cf: CommitFilter) -> dict:
    """Headline metrics for one object."""
    size = commit_set_size(repo_id, cf)
    totals = object_totals(repo_id, kind, path, cf)
    return {**_derive(totals["added"], totals["removed"], totals["modifications"], size),
            "commit_set_size": size}


def by_author(repo_id: int, kind: str, path: str, cf: CommitFilter) -> list[dict]:
    """Per-author breakdown with ownership. The author gate of `cf` is
    ignored here (authors are the breakdown dimension); the time/hash
    filters still apply, and ownership uses the same commit set."""
    base = cf.without_author()
    total_churn = 0
    t = object_totals(repo_id, kind, path, base)
    total_churn = t["added"] + t["removed"]

    join, cond, obj_params = _object_parts(kind, path)
    cf_sql, cf_params = _commit_filter_sql(base)
    conds = [c for c in (cond, cf_sql) if c]
    where = f"AND {' AND '.join(conds)}" if conds else ""
    with db() as conn:
        rows = conn.execute(
            f"""SELECT c.author_id AS author_id,
                       a.name AS name,
                       a.email AS email,
                       COALESCE(SUM(ch.added), 0)   AS added,
                       COALESCE(SUM(ch.removed), 0) AS removed,
                       {_MODS}                      AS modifications
                FROM changes ch
                JOIN commits c ON c.id = ch.commit_id
                JOIN authors a ON a.id = c.author_id
                {join}
                WHERE ch.repo_id = ? {where}
                GROUP BY c.author_id, a.name, a.email""",
            [repo_id, *obj_params, *cf_params],
        ).fetchall()

    out: list[dict] = []
    for row in rows:
        added, removed = int(row["added"]), int(row["removed"])
        churn = added + removed
        out.append(
            {
                "author_id": int(row["author_id"]),
                "name": row["name"],
                "email": row["email"],
                "added": added,
                "removed": removed,
                "growth": added - removed,
                "churn": churn,
                "modifications": int(row["modifications"]),
                "ownership": round(churn / total_churn, 6) if total_churn else 0.0,
            }
        )
    out.sort(key=lambda a: a["churn"], reverse=True)
    return out


def object_timeline(repo_id: int, kind: str, path: str, cf: CommitFilter) -> list[dict]:
    """Monthly added/removed for one object over H."""
    join, cond, obj_params = _object_parts(kind, path)
    cf_sql, cf_params = _commit_filter_sql(cf)
    conds = [c for c in (cond, cf_sql) if c]
    where = f"AND {' AND '.join(conds)}" if conds else ""
    with db() as conn:
        rows = conn.execute(
            f"""SELECT strftime('%Y-%m', c.committer_date, 'unixepoch') AS month,
                       COALESCE(SUM(ch.added), 0)   AS added,
                       COALESCE(SUM(ch.removed), 0) AS removed
                FROM changes ch
                JOIN commits c ON c.id = ch.commit_id
                {join}
                WHERE ch.repo_id = ? {where}
                GROUP BY month
                ORDER BY month""",
            [repo_id, *obj_params, *cf_params],
        ).fetchall()
    return [
        {"month": r["month"], "added": int(r["added"]), "removed": int(r["removed"])}
        for r in rows
    ]


def objects_list(
    repo_id: int,
    kind: str,
    cf: CommitFilter,
    q: str | None = None,
    dir_prefix: str | None = None,
    sort: str = "churn",
    order: str = "desc",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Browsable object list (H[F] / H[D]) with metrics joined in.

    Every known path/directory is listed, even when it has no changes in H
    (metrics are then zero). Directory metrics are subtree sums; the
    repository root ('') is patched in with repository totals.
    """
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))
    sort_col = sort if sort in _SORT_COLUMNS else "churn"
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    cf_sql, cf_params = _commit_filter_sql(cf)
    cf_cond = f"AND {cf_sql}" if cf_sql else ""

    search_cond = ""
    search_params: list = []
    if q:
        search_cond = "AND {alias}.{col} LIKE ? ESCAPE '\\'"
        search_params = [f"%{_like_escape(q)}%"]
    prefix_cond = ""
    prefix_params: list = []
    if dir_prefix is not None and kind == "file":
        prefix_cond = "AND {alias}.path LIKE ? ESCAPE '\\'"
        prefix_params = [f"{_like_escape(dir_prefix)}/%"]

    with db() as conn:
        if kind == "dir":
            fmt = {"alias": "dirs", "col": "dir"}
            agg = f"""
                SELECT d.dir AS adir,
                       COALESCE(SUM(ch.added), 0)   AS added,
                       COALESCE(SUM(ch.removed), 0) AS removed,
                       {_MODS}                      AS modifications
                FROM dirs_of_path d
                JOIN changes ch ON ch.repo_id = d.repo_id AND ch.path = d.path
                JOIN commits c ON c.id = ch.commit_id
                WHERE d.repo_id = ? {cf_cond}
                GROUP BY d.dir"""
            base_cte = (
                "WITH dirs AS (SELECT '' AS dir UNION "
                "SELECT DISTINCT dir FROM dirs_of_path WHERE repo_id = ?)"
            )
            items_sql = f"""
                {base_cte},
                agg AS ({agg})
                SELECT dirs.dir AS dir,
                       COALESCE(agg.added, 0)   AS added,
                       COALESCE(agg.removed, 0) AS removed,
                       COALESCE(agg.added, 0) + COALESCE(agg.removed, 0) AS churn,
                       COALESCE(agg.modifications, 0) AS modifications
                FROM dirs LEFT JOIN agg ON agg.adir = dirs.dir
                WHERE 1 = 1 {search_cond.format(**fmt)} {prefix_cond.format(**fmt)}
                ORDER BY {sort_col} {direction}, dirs.dir ASC
                LIMIT ? OFFSET ?"""
            count_sql = (
                "SELECT COUNT(*) AS n FROM "
                "(SELECT '' AS dir UNION SELECT DISTINCT dir FROM dirs_of_path WHERE repo_id = ?) dirs "
                f"WHERE 1 = 1 {search_cond.format(**fmt)}"
            )
            items_params = [repo_id, repo_id, *cf_params, *search_params, *prefix_params, limit, offset]
            count_params = [repo_id, *search_params]
        else:
            fmt = {"alias": "p", "col": "path"}
            agg = f"""
                SELECT ch.path AS apath,
                       COALESCE(SUM(ch.added), 0)   AS added,
                       COALESCE(SUM(ch.removed), 0) AS removed,
                       {_MODS}                      AS modifications
                FROM changes ch
                JOIN commits c ON c.id = ch.commit_id
                WHERE ch.repo_id = ? {cf_cond}
                GROUP BY ch.path"""
            items_sql = f"""
                WITH agg AS ({agg})
                SELECT p.path AS path,
                       COALESCE(agg.added, 0)   AS added,
                       COALESCE(agg.removed, 0) AS removed,
                       COALESCE(agg.added, 0) + COALESCE(agg.removed, 0) AS churn,
                       COALESCE(agg.modifications, 0) AS modifications
                FROM paths p LEFT JOIN agg ON agg.apath = p.path
                WHERE p.repo_id = ? {search_cond.format(**fmt)} {prefix_cond.format(**fmt)}
                ORDER BY {sort_col} {direction}, p.path ASC
                LIMIT ? OFFSET ?"""
            count_sql = (
                f"SELECT COUNT(*) AS n FROM paths p WHERE p.repo_id = ? "
                f"{search_cond.format(**fmt)} {prefix_cond.format(**fmt)}"
            )
            items_params = [repo_id, *cf_params, repo_id, *search_params, *prefix_params, limit, offset]
            count_params = [repo_id, *search_params, *prefix_params]

        rows = conn.execute(items_sql, items_params).fetchall()
        total = int(conn.execute(count_sql, count_params).fetchone()["n"])

    items = [dict(r) for r in rows]
    if kind == "dir":
        for item in items:
            if item["dir"] == "":
                t = object_totals(repo_id, "repo", "", cf)
                item.update(
                    added=t["added"], removed=t["removed"],
                    churn=t["added"] + t["removed"], modifications=t["modifications"],
                )
    return {"kind": kind, "total": total, "limit": limit, "offset": offset, "items": items}


def overview(repo_id: int, cf: CommitFilter, top_n: int = 10) -> dict:
    """Dashboard payload: repo info, commit-set stats, timeline, top lists."""
    with db() as conn:
        repo = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
        history_size = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM commits WHERE repo_id = ?", (repo_id,)
            ).fetchone()["n"]
        )
    if repo is None:
        raise LookupError("Repository not found")

    size = commit_set_size(repo_id, cf)
    totals = object_totals(repo_id, "repo", "", cf)
    totals = _derive(totals["added"], totals["removed"], totals["modifications"], size)

    cf_sql, cf_params = _commit_filter_sql(cf)
    cf_cond = f"AND {cf_sql}" if cf_sql else ""
    with db() as conn:
        commit_rows = conn.execute(
            f"""SELECT strftime('%Y-%m', c.committer_date, 'unixepoch') AS month,
                       COUNT(*) AS commits
                FROM commits c
                WHERE c.repo_id = ? {cf_cond}
                GROUP BY month ORDER BY month""",
            [repo_id, *cf_params],
        ).fetchall()
        change_rows = conn.execute(
            f"""SELECT strftime('%Y-%m', c.committer_date, 'unixepoch') AS month,
                       COALESCE(SUM(ch.added), 0)   AS added,
                       COALESCE(SUM(ch.removed), 0) AS removed
                FROM changes ch
                JOIN commits c ON c.id = ch.commit_id
                WHERE ch.repo_id = ? {cf_cond}
                GROUP BY month ORDER BY month""",
            [repo_id, *cf_params],
        ).fetchall()
        top_files = conn.execute(
            f"""SELECT ch.path AS path,
                       COALESCE(SUM(ch.added), 0)   AS added,
                       COALESCE(SUM(ch.removed), 0) AS removed,
                       {_MODS}                      AS modifications
                FROM changes ch
                JOIN commits c ON c.id = ch.commit_id
                WHERE ch.repo_id = ? {cf_cond}
                GROUP BY ch.path
                ORDER BY COALESCE(SUM(ch.added), 0) + COALESCE(SUM(ch.removed), 0) DESC
                LIMIT ?""",
            [repo_id, *cf_params, top_n],
        ).fetchall()
        top_dirs = conn.execute(
            f"""SELECT d.dir AS dir,
                       COALESCE(SUM(ch.added), 0)   AS added,
                       COALESCE(SUM(ch.removed), 0) AS removed,
                       {_MODS}                      AS modifications
                FROM dirs_of_path d
                JOIN changes ch ON ch.repo_id = d.repo_id AND ch.path = d.path
                JOIN commits c ON c.id = ch.commit_id
                WHERE d.repo_id = ? {cf_cond}
                GROUP BY d.dir
                ORDER BY COALESCE(SUM(ch.added), 0) + COALESCE(SUM(ch.removed), 0) DESC
                LIMIT ?""",
            [repo_id, *cf_params, top_n],
        ).fetchall()

    timeline: dict[str, dict] = {}
    for r in commit_rows:
        timeline[r["month"]] = {
            "month": r["month"], "commits": int(r["commits"]), "added": 0, "removed": 0,
        }
    for r in change_rows:
        entry = timeline.setdefault(
            r["month"], {"month": r["month"], "commits": 0, "added": 0, "removed": 0}
        )
        entry["added"] = int(r["added"])
        entry["removed"] = int(r["removed"])

    def _rows(rows_, key: str) -> list[dict]:
        out = []
        for r in rows_:
            added, removed = int(r["added"]), int(r["removed"])
            row = {
                key: r[key],
                "added": added,
                "removed": removed,
                "growth": added - removed,
                "churn": added + removed,
                "modifications": int(r["modifications"]),
            }
            if key == "dir" and row["dir"] == "":
                row["dir"] = "(root)"
            out.append(row)
        return out

    # ownership for the top authors is relative to the repository churn over
    # the same time/hash window (author gate removed)
    base_total = object_totals(repo_id, "repo", "", cf.without_author())
    total_churn = base_total["added"] + base_total["removed"]
    top_authors = by_author(repo_id, "repo", "", cf)[:top_n]
    for a in top_authors:
        a["ownership"] = round(a["churn"] / total_churn, 6) if total_churn else 0.0

    return {
        "repo": {
            "id": int(repo["id"]),
            "name": repo["name"],
            "source": repo["source"],
            "source_uri": repo["source_uri"],
            "reference_commit": repo["reference_commit"],
            "history_size": history_size,
        },
        "commit_set": {
            "size": size,
            "from_ts": cf.from_ts,
            "to_ts": cf.to_ts,
            "hashes": len(cf.hashes) if cf.hashes is not None else None,
            "author_id": cf.author_id,
        },
        "totals": totals,
        "timeline": [timeline[m] for m in sorted(timeline)],
        "top_files": _rows(top_files, "path"),
        "top_dirs": [r for r in _rows(top_dirs, "dir") if r["dir"] != "(root)"],
        "top_authors": top_authors,
    }


# ---------------------------------------------------------------------------
# daily_activity  — calendar heatmap data
# ---------------------------------------------------------------------------

def daily_activity(repo_id: int, cf: CommitFilter) -> list[dict]:
    """Return [{date, count}, ...] for every day with at least one commit."""
    cf_sql, cf_params = _commit_filter_sql(cf)
    where = f"AND {cf_sql}" if cf_sql else ""
    sql = f"""
        SELECT strftime('%Y-%m-%d', c.committer_date, 'unixepoch') AS date,
               COUNT(*) AS count
          FROM commits c
         WHERE c.repo_id = ? {where}
         GROUP BY date
         ORDER BY date
    """
    with db() as conn:
        return [{"date": r["date"], "count": r["count"]}
                for r in conn.execute(sql, [repo_id, *cf_params])]


# ---------------------------------------------------------------------------
# dir_treemap  — hierarchical directory structure with metrics
# ---------------------------------------------------------------------------

def dir_treemap(repo_id: int, cf: CommitFilter) -> list[dict]:
    """Build an ECharts-ready tree: [{name, value, children}, ...]."""
    cf_sql, cf_params = _commit_filter_sql(cf)
    where = f"AND {cf_sql}" if cf_sql else ""
    sql = f"""
        SELECT d.dir,
               SUM(ch.added + ch.removed) AS churn,
               SUM(ch.added)              AS added,
               SUM(ch.removed)            AS removed,
               {_MODS}                    AS modifications
          FROM changes ch
          JOIN commits c ON c.id = ch.commit_id AND c.repo_id = ch.repo_id
          JOIN dirs_of_path d ON d.repo_id = ch.repo_id AND d.path = ch.path
         WHERE ch.repo_id = ? {where}
         GROUP BY d.dir
    """
    with db() as conn:
        rows = {r["dir"]: dict(r) for r in conn.execute(sql, [repo_id, *cf_params]) if r["dir"]}
    if not rows:
        return []

    # direct contributions per directory (subtract children)
    dirs_sorted = sorted(rows.keys())
    children_churn: dict[str, int] = {}
    for d in dirs_sorted:
        parent = d.rsplit("/", 1)[0] if "/" in d else ""
        if parent in rows:
            children_churn[parent] = children_churn.get(parent, 0) + rows[d]["churn"]

    # build the tree bottom-up
    nodes: dict[str, dict] = {}
    for d in sorted(dirs_sorted, key=lambda x: x.count("/"), reverse=True):
        r = rows[d]
        own_churn = max(0, r["churn"] - children_churn.get(d, 0))
        node: dict = {"name": d.rsplit("/", 1)[-1] if "/" in d else d,
                       "path": d, "value": own_churn,
                       "added": r["added"], "removed": r["removed"],
                       "churn": r["churn"], "modifications": r["modifications"]}
        kids = [nodes[c] for c in dirs_sorted if _is_direct_child(d, c) and c in nodes]
        if kids:
            node["children"] = kids
        nodes[d] = node

    # collect top-level roots
    roots = [nodes[d] for d in dirs_sorted if "/" not in d and d in nodes]
    return roots


def _is_direct_child(parent: str, candidate: str) -> bool:
    return candidate.startswith(parent + "/") and "/" not in candidate[len(parent) + 1:]
