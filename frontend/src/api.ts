export interface Repo {
  id: number;
  name: string;
  source: "zip" | "url";
  source_uri: string | null;
  repo_path: string | null;
  reference_commit: string | null;
  requested_ref: string | null;
  status: "created" | "ingesting" | "ready" | "error";
  progress: number;
  error: string | null;
  commit_count: number;
  created_at: string;
}

export interface Totals {
  added: number;
  removed: number;
  growth: number;
  churn: number;
  modifications: number;
  frequency: number;
  churn_rate: number;
}

export interface ListRow {
  path?: string;
  dir?: string;
  added: number;
  removed: number;
  churn: number;
  modifications: number;
}

export interface ObjectAuthor {
  author_id: number;
  name: string;
  email: string;
  added: number;
  removed: number;
  growth: number;
  churn: number;
  modifications: number;
  ownership: number;
}

export interface TimelinePoint {
  month: string;
  commits: number;
  added: number;
  removed: number;
}

export interface Overview {
  repo: {
    id: number;
    name: string;
    source: string;
    source_uri: string | null;
    reference_commit: string | null;
    history_size: number;
  };
  commit_set: {
    size: number;
    from_ts: number | null;
    to_ts: number | null;
    hashes: number | null;
    author_id: number | null;
  };
  totals: Totals;
  timeline: TimelinePoint[];
  top_files: (ListRow & { path: string })[];
  top_dirs: (ListRow & { dir: string })[];
  top_authors: ObjectAuthor[];
}

export interface ObjectsPage {
  kind: "file" | "dir";
  total: number;
  limit: number;
  offset: number;
  items: ListRow[];
}

export interface ObjectDetail {
  kind: "file" | "dir" | "repo";
  path: string;
  commit_set: {
    size: number;
    from_ts: number | null;
    to_ts: number | null;
    hashes: number | null;
    author_id: number | null;
  };
  metrics: Totals;
  authors: ObjectAuthor[];
  timeline: { month: string; added: number; removed: number }[];
}

export interface Author {
  id: number;
  name: string;
  email: string;
  commit_count: number;
  first_commit: number | null;
  last_commit: number | null;
}

export interface MetricParams {
  from_ts?: number | null;
  to_ts?: number | null;
  commits?: string | null;
  author_id?: number | null;
}

export interface ListParams extends MetricParams {
  q?: string;
  dir?: string;
  sort?: string;
  order?: string;
  limit?: number;
  offset?: number;
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail ?? `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

function qs(params: object): string {
  const parts = Object.entries(params as Record<string, unknown>)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

// ---- repositories ----------------------------------------------------------

export const listRepos = (): Promise<Repo[]> =>
  fetch("/api/repos").then((r) => handle<Repo[]>(r));

export const uploadZip = (
  file: File,
  name: string,
  ref?: string
): Promise<{ repo_id: number }> => {
  const form = new FormData();
  form.append("file", file);
  if (name) form.append("name", name);
  if (ref) form.append("ref", ref);
  return fetch("/api/repos/upload", { method: "POST", body: form }).then((r) =>
    handle<{ repo_id: number }>(r)
  );
};

export const cloneRepo = (
  url: string,
  name: string,
  ref?: string
): Promise<{ repo_id: number }> =>
  fetch("/api/repos/clone", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, name: name || null, ref: ref || null }),
  }).then((r) => handle<{ repo_id: number }>(r));

export const deleteRepo = async (id: number): Promise<void> => {
  const res = await fetch(`/api/repos/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Delete failed (${res.status})`);
};

// ---- metrics ---------------------------------------------------------------

export const getOverview = (id: number, p: MetricParams = {}): Promise<Overview> =>
  fetch(`/api/repos/${id}/metrics/overview${qs(p)}`).then((r) => handle<Overview>(r));

export const getObjects = (
  id: number,
  kind: "file" | "dir",
  p: ListParams = {}
): Promise<ObjectsPage> =>
  fetch(`/api/repos/${id}/metrics/objects${qs({ ...p, kind })}`).then((r) =>
    handle<ObjectsPage>(r)
  );

export const getObject = (
  id: number,
  kind: "file" | "dir" | "repo",
  path: string,
  p: MetricParams = {}
): Promise<ObjectDetail> =>
  fetch(`/api/repos/${id}/metrics/object${qs({ ...p, kind, path })}`).then((r) =>
    handle<ObjectDetail>(r)
  );

// ---- authors ---------------------------------------------------------------

export const listAuthors = (id: number): Promise<Author[]> =>
  fetch(`/api/repos/${id}/authors`).then((r) => handle<Author[]>(r));

export const mergeAuthors = (
  id: number,
  target: number,
  sources: number[]
): Promise<{ merged: number; target_author_id: number }> =>
  fetch(`/api/repos/${id}/authors/merge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_author_id: target, source_author_ids: sources }),
  }).then((r) => handle<{ merged: number; target_author_id: number }>(r));
