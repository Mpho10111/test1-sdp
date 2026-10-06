import { useEffect, useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import {
  getObject,
  getObjects,
  getOverview,
  listAuthors,
  mergeAuthors,
  type Author,
  type MetricParams,
  type ObjectAuthor,
  type ObjectDetail,
  type ObjectsPage,
  type Overview,
  type Repo,
} from "./api";
import EChart from "./EChart";

type Tab = "overview" | "files" | "dirs" | "authors" | "object";
type ObjKind = "file" | "dir" | "repo";

const PAGE = 50;
const n = (v: number) => v.toLocaleString("en-US");
const f2 = (v: number) => v.toFixed(2);
const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
const tsFromDate = (s: string) =>
  s ? Math.floor(new Date(`${s}T00:00:00`).getTime() / 1000) : null;
const dt = (ts: number | null) =>
  ts ? new Date(ts * 1000).toLocaleDateString("en-US") : "—";

function monthlyOption(
  points: { month: string; commits?: number; added: number; removed: number }[]
): EChartsOption {
  const hasCommits = points.some((p) => p.commits !== undefined);
  return {
    tooltip: { trigger: "axis" },
    legend: { top: 0 },
    grid: { left: 60, right: 60, top: 36, bottom: 28 },
    xAxis: {
      type: "category",
      data: points.map((p) => p.month),
      axisLabel: { fontSize: 11 },
    },
    yAxis: [
      { type: "value", name: "commits", show: hasCommits },
      { type: "value", name: "lines" },
    ],
    series: [
      ...(hasCommits
        ? [
            {
              name: "commits",
              type: "bar" as const,
              yAxisIndex: 0,
              data: points.map((p) => p.commits ?? 0),
              itemStyle: { color: "#c9d8f5" },
            },
          ]
        : []),
      {
        name: "added",
        type: "line" as const,
        smooth: true,
        showSymbol: false,
        yAxisIndex: 1,
        data: points.map((p) => p.added),
        itemStyle: { color: "#16a34a" },
        lineStyle: { width: 2 },
      },
      {
        name: "removed",
        type: "line" as const,
        smooth: true,
        showSymbol: false,
        yAxisIndex: 1,
        data: points.map((p) => p.removed),
        itemStyle: { color: "#dc2626" },
        lineStyle: { width: 2 },
      },
    ],
  };
}

function StatCards({ items }: { items: { label: string; value: string; hint?: string }[] }) {
  return (
    <div className="stat-grid">
      {items.map((it) => (
        <div className="stat-card" key={it.label}>
          <div className="v">{it.value}</div>
          <div className="l">{it.label}</div>
          {it.hint && <div className="hint">{it.hint}</div>}
        </div>
      ))}
    </div>
  );
}

function SortTh({
  label,
  col,
  sort,
  order,
  onSort,
  num,
}: {
  label: string;
  col: string;
  sort: string;
  order: string;
  onSort: (col: string) => void;
  num?: boolean;
}) {
  const active = sort === col;
  return (
    <th className={`sortable${num ? " num" : ""}`} onClick={() => onSort(col)}>
      {label}
      <span className="arrow">{active ? (order === "desc" ? " ▾" : " ▴") : ""}</span>
    </th>
  );
}

function Pager({
  page,
  total,
  limit,
  onPrev,
  onNext,
}: {
  page: number;
  total: number;
  limit: number;
  onPrev: () => void;
  onNext: () => void;
}) {
  return (
    <div className="pager">
      <button className="ghost" disabled={page <= 0} onClick={onPrev}>
        ← Prev
      </button>
      <span className="muted">
        {total === 0 ? "0" : `${page + 1}–${Math.min(page + limit, total)}`} of {n(total)}
      </span>
      <button className="ghost" disabled={page + limit >= total} onClick={onNext}>
        Next →
      </button>
    </div>
  );
}

function AuthorTable({
  authors,
  onPick,
  highlightId,
}: {
  authors: ObjectAuthor[];
  onPick?: (id: number) => void;
  highlightId?: number | null;
}) {
  if (authors.length === 0) return <p className="muted">No authors in this commit set.</p>;
  return (
    <table className="compact">
      <thead>
        <tr>
          <th>Author</th>
          <th>Email</th>
          <th className="num">Added</th>
          <th className="num">Removed</th>
          <th className="num">Churn</th>
          <th className="num">Mods</th>
          <th className="num">Ownership</th>
        </tr>
      </thead>
      <tbody>
        {authors.map((a) => (
          <tr
            key={a.author_id}
            className={`${onPick ? "clickable" : ""}${highlightId === a.author_id ? " hl" : ""}`}
            onClick={() => onPick?.(a.author_id)}
          >
            <td>{a.name}</td>
            <td className="muted">{a.email}</td>
            <td className="num pos">+{n(a.added)}</td>
            <td className="num neg">−{n(a.removed)}</td>
            <td className="num">{n(a.churn)}</td>
            <td className="num">{n(a.modifications)}</td>
            <td className="num">{pct(a.ownership)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AuthorsPanel({
  authors,
  repoId,
  onMerged,
  onError,
}: {
  authors: Author[];
  repoId: number;
  onMerged: () => void;
  onError: (msg: string) => void;
}) {
  const [target, setTarget] = useState<number | null>(null);
  const [sources, setSources] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);

  const toggle = (id: number) => {
    setSources((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    if (id === target) setTarget(null);
  };

  const doMerge = async () => {
    if (target == null) return;
    const src = [...sources].filter((i) => i !== target);
    if (src.length === 0) return;
    setBusy(true);
    try {
      await mergeAuthors(repoId, target, src);
      setSources(new Set());
      setTarget(null);
      onMerged();
    } catch (e) {
      onError(e instanceof Error ? e.message : "Merge failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel">
      <div className="panel-bar">
        <button disabled={busy || target == null || sources.size === 0} onClick={() => void doMerge()}>
          Merge selected into target
        </button>
        <span className="muted">
          Tick the identities to absorb, pick the target with ◎ (e.g. same person, different emails)
        </span>
      </div>
      {authors.length === 0 ? (
        <p className="muted">No authors.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th></th>
              <th>Target</th>
              <th>Name</th>
              <th>Email</th>
              <th className="num">Commits</th>
              <th>First</th>
              <th>Last</th>
            </tr>
          </thead>
          <tbody>
            {authors.map((a) => (
              <tr key={a.id} className={target === a.id ? "hl" : ""}>
                <td>
                  <input
                    type="checkbox"
                    checked={sources.has(a.id)}
                    onChange={() => toggle(a.id)}
                  />
                </td>
                <td>
                  <input
                    type="radio"
                    name="merge-target"
                    checked={target === a.id}
                    onChange={() => setTarget(a.id)}
                  />
                </td>
                <td>{a.name}</td>
                <td className="muted">{a.email}</td>
                <td className="num">{n(a.commit_count)}</td>
                <td className="muted">{dt(a.first_commit)}</td>
                <td className="muted">{dt(a.last_commit)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export default function RepoDetail({ repo, onBack }: { repo: Repo; onBack: () => void }) {
  const [tab, setTab] = useState<Tab>("overview");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [authorId, setAuthorId] = useState("");
  const [hashList, setHashList] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [authors, setAuthors] = useState<Author[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);

  const [fileQ, setFileQ] = useState("");
  const [fileSort, setFileSort] = useState("churn");
  const [fileOrder, setFileOrder] = useState("desc");
  const [fileOffset, setFileOffset] = useState(0);
  const [files, setFiles] = useState<ObjectsPage | null>(null);

  const [dirQ, setDirQ] = useState("");
  const [dirSort, setDirSort] = useState("churn");
  const [dirOrder, setDirOrder] = useState("desc");
  const [dirOffset, setDirOffset] = useState(0);
  const [dirs, setDirs] = useState<ObjectsPage | null>(null);

  const [objSel, setObjSel] = useState<{ kind: ObjKind; path: string }>({
    kind: "repo",
    path: "",
  });
  const [objKindInput, setObjKindInput] = useState<ObjKind>("file");
  const [objPathInput, setObjPathInput] = useState("");
  const [object, setObject] = useState<ObjectDetail | null>(null);

  const filters: MetricParams = useMemo(
    () => ({
      from_ts: tsFromDate(fromDate),
      to_ts: tsFromDate(toDate),
      author_id: authorId ? Number(authorId) : null,
      commits: hashList.trim() ? hashList.trim() : null,
    }),
    [fromDate, toDate, authorId, hashList]
  );

  useEffect(() => {
    let stop = false;
    listAuthors(repo.id)
      .then((d) => {
        if (!stop) setAuthors(d);
      })
      .catch((e: Error) => {
        if (!stop) setLoadError(e.message);
      });
    return () => {
      stop = true;
    };
  }, [repo.id, reloadKey]);

  useEffect(() => {
    let stop = false;
    getOverview(repo.id, filters)
      .then((d) => {
        if (!stop) {
          setOverview(d);
          setLoadError(null);
        }
      })
      .catch((e: Error) => {
        if (!stop) setLoadError(e.message);
      });
    return () => {
      stop = true;
    };
  }, [repo.id, filters, reloadKey]);

  useEffect(() => {
    if (tab !== "files") return;
    let stop = false;
    getObjects(repo.id, "file", {
      ...filters,
      q: fileQ || undefined,
      sort: fileSort,
      order: fileOrder,
      limit: PAGE,
      offset: fileOffset,
    })
      .then((d) => {
        if (!stop) setFiles(d);
      })
      .catch((e: Error) => {
        if (!stop) setLoadError(e.message);
      });
    return () => {
      stop = true;
    };
  }, [tab, repo.id, filters, fileQ, fileSort, fileOrder, fileOffset, reloadKey]);

  useEffect(() => {
    if (tab !== "dirs") return;
    let stop = false;
    getObjects(repo.id, "dir", {
      ...filters,
      q: dirQ || undefined,
      sort: dirSort,
      order: dirOrder,
      limit: PAGE,
      offset: dirOffset,
    })
      .then((d) => {
        if (!stop) setDirs(d);
      })
      .catch((e: Error) => {
        if (!stop) setLoadError(e.message);
      });
    return () => {
      stop = true;
    };
  }, [tab, repo.id, filters, dirQ, dirSort, dirOrder, dirOffset, reloadKey]);

  useEffect(() => {
    if (tab !== "object") return;
    let stop = false;
    getObject(repo.id, objSel.kind, objSel.path, filters)
      .then((d) => {
        if (!stop) setObject(d);
      })
      .catch((e: Error) => {
        if (!stop) setLoadError(e.message);
      });
    return () => {
      stop = true;
    };
  }, [tab, repo.id, objSel, filters, reloadKey]);

  const openObject = (kind: ObjKind, path: string) => {
    setObjSel({ kind, path });
    setObjKindInput(kind);
    setObjPathInput(path);
    setTab("object");
  };

  const pickAuthor = (id: number) => {
    setAuthorId((cur) => (cur === String(id) ? "" : String(id)));
  };

  const makeSort =
    (
      cur: string,
      order: string,
      setSort: (s: string) => void,
      setOrder: (o: string) => void,
      setOffset: (o: number) => void
    ) =>
    (col: string) => {
      if (col === cur) setOrder(order === "desc" ? "asc" : "desc");
      else {
        setSort(col);
        setOrder(col === "path" ? "asc" : "desc");
      }
      setOffset(0);
    };

  const sortFiles = makeSort(fileSort, fileOrder, setFileSort, setFileOrder, setFileOffset);
  const sortDirs = makeSort(dirSort, dirOrder, setDirSort, setDirOrder, setDirOffset);
  const cleared = !fromDate && !toDate && !authorId && !hashList.trim();

  return (
    <div className="page">
      <header className="dash-head">
        <button className="link" onClick={onBack}>
          ← All repositories
        </button>
        <h1>{repo.name}</h1>
        <span className="muted">
          {repo.source_uri} · ref {(repo.reference_commit ?? "").slice(0, 10)}
        </span>
      </header>

      {loadError && <div className="banner error">{loadError}</div>}

      <div className="filter-bar">
        <label>
          From
          <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
        </label>
        <label>
          To
          <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} />
        </label>
        <label>
          Author
          <select value={authorId} onChange={(e) => setAuthorId(e.target.value)}>
            <option value="">All authors</option>
            {authors.map((a) => (
              <option key={a.id} value={a.id}>{`${a.name} <${a.email}> (${a.commit_count})`}</option>
            ))}
          </select>
        </label>
        <label className="grow">
          Commit list (optional)
          <input
            placeholder="hash1, hash2, …"
            value={hashList}
            onChange={(e) => setHashList(e.target.value)}
          />
        </label>
        {!cleared && (
          <button
            className="ghost"
            onClick={() => {
              setFromDate("");
              setToDate("");
              setAuthorId("");
              setHashList("");
            }}
          >
            Clear filters
          </button>
        )}
        {overview && (
          <span className="pill">
            H = {n(overview.commit_set.size)} of {n(overview.repo.history_size)} commits
          </span>
        )}
      </div>

      <nav className="tabs">
        {(
          [
            ["overview", "Overview"],
            ["files", "Files"],
            ["dirs", "Directories"],
            ["authors", "Authors"],
            ["object", "Object inspector"],
          ] as [Tab, string][]
        ).map(([id, label]) => (
          <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </nav>

      {tab === "overview" &&
        (overview ? (
          <>
            <StatCards
              items={[
                { label: "Commits in H", value: n(overview.commit_set.size), hint: `of ${n(overview.repo.history_size)} total` },
                { label: "Added", value: `+${n(overview.totals.added)}` },
                { label: "Removed", value: `−${n(overview.totals.removed)}` },
                { label: "Growth (δ)", value: n(overview.totals.growth) },
                { label: "Churn (λ)", value: n(overview.totals.churn) },
                { label: "Modifying commits", value: n(overview.totals.modifications) },
                { label: "Frequency (η)", value: f2(overview.totals.frequency) },
                { label: "Churn rate (ρ)", value: f2(overview.totals.churn_rate) },
              ]}
            />
            <div className="chart-card">
              <h3>Activity per month</h3>
              <EChart option={monthlyOption(overview.timeline)} height={300} />
            </div>
            <div className="grid-2">
              <div className="chart-card">
                <h3>Top files by churn</h3>
                <table className="compact">
                  <thead>
                    <tr>
                      <th>Path</th>
                      <th className="num">Churn</th>
                      <th className="num">Mods</th>
                    </tr>
                  </thead>
                  <tbody>
                    {overview.top_files.map((r) => (
                      <tr key={r.path} className="clickable" onClick={() => openObject("file", r.path)}>
                        <td className="mono">{r.path}</td>
                        <td className="num">{n(r.churn)}</td>
                        <td className="num">{n(r.modifications)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="chart-card">
                <h3>Top directories by churn</h3>
                <table className="compact">
                  <thead>
                    <tr>
                      <th>Directory</th>
                      <th className="num">Churn</th>
                      <th className="num">Mods</th>
                    </tr>
                  </thead>
                  <tbody>
                    {overview.top_dirs.map((r) => (
                      <tr key={r.dir} className="clickable" onClick={() => openObject("dir", r.dir)}>
                        <td className="mono">{r.dir}/</td>
                        <td className="num">{n(r.churn)}</td>
                        <td className="num">{n(r.modifications)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="chart-card">
              <h3>
                Top authors by churn <span className="muted">— click a row to filter the whole view</span>
              </h3>
              <AuthorTable
                authors={overview.top_authors}
                onPick={pickAuthor}
                highlightId={authorId ? Number(authorId) : null}
              />
            </div>
          </>
        ) : (
          <p className="muted">Loading overview…</p>
        ))}

      {tab === "files" && (
        <section className="panel">
          <div className="panel-bar">
            <input
              placeholder="Filter by path…"
              value={fileQ}
              onChange={(e) => {
                setFileQ(e.target.value);
                setFileOffset(0);
              }}
            />
            <span className="muted">Every file known to the history — click a row to inspect it</span>
          </div>
          {files && files.items.length > 0 ? (
            <>
              <table>
                <thead>
                  <tr>
                    <SortTh label="Path" col="path" sort={fileSort} order={fileOrder} onSort={sortFiles} />
                    <SortTh label="Added" col="added" sort={fileSort} order={fileOrder} onSort={sortFiles} num />
                    <SortTh label="Removed" col="removed" sort={fileSort} order={fileOrder} onSort={sortFiles} num />
                    <th className="num">Growth</th>
                    <SortTh label="Churn" col="churn" sort={fileSort} order={fileOrder} onSort={sortFiles} num />
                    <SortTh label="Mods" col="modifications" sort={fileSort} order={fileOrder} onSort={sortFiles} num />
                  </tr>
                </thead>
                <tbody>
                  {files.items.map((row) => (
                    <tr key={row.path} className="clickable" onClick={() => openObject("file", row.path!)}>
                      <td className="mono">{row.path}</td>
                      <td className="num pos">+{n(row.added)}</td>
                      <td className="num neg">−{n(row.removed)}</td>
                      <td className="num">{n(row.added - row.removed)}</td>
                      <td className="num">{n(row.churn)}</td>
                      <td className="num">{n(row.modifications)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Pager
                page={fileOffset}
                total={files.total}
                limit={PAGE}
                onPrev={() => setFileOffset(Math.max(0, fileOffset - PAGE))}
                onNext={() => setFileOffset(fileOffset + PAGE)}
              />
            </>
          ) : (
            <p className="muted">No files match.</p>
          )}
        </section>
      )}

      {tab === "dirs" && (
        <section className="panel">
          <div className="panel-bar">
            <input
              placeholder="Filter by directory…"
              value={dirQ}
              onChange={(e) => {
                setDirQ(e.target.value);
                setDirOffset(0);
              }}
            />
            <span className="muted">Directory metrics are subtree sums — click a row to inspect it</span>
          </div>
          {dirs && dirs.items.length > 0 ? (
            <>
              <table>
                <thead>
                  <tr>
                    <SortTh label="Directory" col="path" sort={dirSort} order={dirOrder} onSort={sortDirs} />
                    <SortTh label="Added" col="added" sort={dirSort} order={dirOrder} onSort={sortDirs} num />
                    <SortTh label="Removed" col="removed" sort={dirSort} order={dirOrder} onSort={sortDirs} num />
                    <th className="num">Growth</th>
                    <SortTh label="Churn" col="churn" sort={dirSort} order={dirOrder} onSort={sortDirs} num />
                    <SortTh label="Mods" col="modifications" sort={dirSort} order={dirOrder} onSort={sortDirs} num />
                  </tr>
                </thead>
                <tbody>
                  {dirs.items.map((row) => (
                    <tr
                      key={row.dir || "(root)"}
                      className="clickable"
                      onClick={() => openObject("dir", row.dir ?? "")}
                    >
                      <td className="mono">{row.dir ? `${row.dir}/` : "(root)"}</td>
                      <td className="num pos">+{n(row.added)}</td>
                      <td className="num neg">−{n(row.removed)}</td>
                      <td className="num">{n(row.added - row.removed)}</td>
                      <td className="num">{n(row.churn)}</td>
                      <td className="num">{n(row.modifications)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Pager
                page={dirOffset}
                total={dirs.total}
                limit={PAGE}
                onPrev={() => setDirOffset(Math.max(0, dirOffset - PAGE))}
                onNext={() => setDirOffset(dirOffset + PAGE)}
              />
            </>
          ) : (
            <p className="muted">No directories match.</p>
          )}
        </section>
      )}

      {tab === "authors" && (
        <AuthorsPanel
          authors={authors}
          repoId={repo.id}
          onMerged={() => setReloadKey((k) => k + 1)}
          onError={setLoadError}
        />
      )}

      {tab === "object" && (
        <section className="panel">
          <div className="panel-bar">
            <select value={objKindInput} onChange={(e) => setObjKindInput(e.target.value as ObjKind)}>
              <option value="file">File</option>
              <option value="dir">Directory</option>
              <option value="repo">Repository (root)</option>
            </select>
            <input
              placeholder={objKindInput === "repo" ? "(whole repository)" : "path/to/object"}
              value={objPathInput}
              disabled={objKindInput === "repo"}
              onChange={(e) => setObjPathInput(e.target.value)}
            />
            <button
              disabled={objKindInput !== "repo" && !objPathInput.trim()}
              onClick={() =>
                openObject(objKindInput, objKindInput === "repo" ? "" : objPathInput.trim())
              }
            >
              Inspect
            </button>
            <span className="muted">
              current: {objSel.kind} {objSel.path || "(root)"}
            </span>
          </div>
          {object ? (
            <>
              <StatCards
                items={[
                  { label: "Commits in H", value: n(object.commit_set.size) },
                  { label: "Added", value: `+${n(object.metrics.added)}` },
                  { label: "Removed", value: `−${n(object.metrics.removed)}` },
                  { label: "Growth (δ)", value: n(object.metrics.growth) },
                  { label: "Churn (λ)", value: n(object.metrics.churn) },
                  { label: "Modifications", value: n(object.metrics.modifications) },
                  { label: "Frequency (η)", value: f2(object.metrics.frequency) },
                  { label: "Churn rate (ρ)", value: f2(object.metrics.churn_rate) },
                ]}
              />
              {object.timeline.length > 0 && (
                <div className="chart-card">
                  <h3>Monthly churn</h3>
                  <EChart option={monthlyOption(object.timeline)} height={260} />
                </div>
              )}
              <div className="chart-card">
                <h3>
                  Authors <span className="muted">— click a row to filter the whole view</span>
                </h3>
                <AuthorTable
                  authors={object.authors}
                  onPick={pickAuthor}
                  highlightId={authorId ? Number(authorId) : null}
                />
              </div>
            </>
          ) : (
            <p className="muted">Pick an object and press Inspect.</p>
          )}
        </section>
      )}
    </div>
  );
}
