import { useCallback, useEffect, useRef, useState } from "react";
import { cloneRepo, deleteRepo, listRepos, uploadZip, type Repo } from "./api";
import RepoDetail from "./RepoDetail";

export default function App() {
  const [repos, setRepos] = useState<Repo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [urlName, setUrlName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [zipName, setZipName] = useState("");
  const [busy, setBusy] = useState(false);
  const [activeRepo, setActiveRepo] = useState<Repo | null>(null);
  const pollTimer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      setRepos(await listRepos());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load repositories");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const anyActive = repos.some((r) => r.status === "ingesting" || r.status === "created");
  useEffect(() => {
    if (!anyActive) return;
    pollTimer.current = window.setInterval(() => void refresh(), 2000);
    return () => {
      if (pollTimer.current) window.clearInterval(pollTimer.current);
    };
  }, [anyActive, refresh]);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setBusy(false);
    }
  };

  const active = activeRepo
    ? repos.find((r) => r.id === activeRepo.id) ?? activeRepo
    : null;
  if (active) {
    return <RepoDetail repo={active} onBack={() => setActiveRepo(null)} />;
  }

  return (
    <div className="page">
      <header>
        <h1>RAT — Repo Analysis Tool</h1>
        <p>Analyze Git repositories per author, file, directory, and commit set.</p>
      </header>

      {error && <div className="banner error">{error}</div>}

      <section className="cards">
        <form
          className="card"
          onSubmit={(e) => {
            e.preventDefault();
            if (!file) return;
            void run(() => uploadZip(file, zipName));
          }}
        >
          <h2>Upload zip</h2>
          <p>Zip of a repository including its .git directory or file.</p>
          <input type="file" accept=".zip" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <input placeholder="Name (optional)" value={zipName} onChange={(e) => setZipName(e.target.value)} />
          <button disabled={busy || !file}>Upload</button>
        </form>

        <form
          className="card"
          onSubmit={(e) => {
            e.preventDefault();
            void run(() => cloneRepo(url, urlName));
          }}
        >
          <h2>Clone from URL</h2>
          <p>Full clone (all history) from a remote repository URL.</p>
          <input placeholder="https://github.com/org/repo.git" value={url} onChange={(e) => setUrl(e.target.value)} />
          <input placeholder="Name (optional)" value={urlName} onChange={(e) => setUrlName(e.target.value)} />
          <button disabled={busy || !url}>Clone</button>
        </form>
      </section>

      <section>
        <h2>Repositories</h2>
        {repos.length === 0 ? (
          <p className="muted">No repositories yet — upload or clone one above.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Source</th>
                <th>Status</th>
                <th>Commits</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {repos.map((repo) => (
                <tr key={repo.id}>
                  <td>{repo.name}</td>
                  <td className="muted">{repo.source === "zip" ? "zip" : repo.source_uri}</td>
                  <td>
                    <span className={`badge ${repo.status}`}>{repo.status}</span>
                    {repo.status === "ingesting" && (
                      <span className="muted"> {Math.round(repo.progress * 100)}%</span>
                    )}
                    {repo.status === "error" && repo.error && <div className="error-text">{repo.error}</div>}
                  </td>
                  <td>{repo.commit_count}</td>
                  <td className="actions">
                    {repo.status === "ready" && (
                      <button className="link" onClick={() => setActiveRepo(repo)}>
                        Open dashboard
                      </button>
                    )}
                    <button className="link" onClick={() => void run(() => deleteRepo(repo.id))}>
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
