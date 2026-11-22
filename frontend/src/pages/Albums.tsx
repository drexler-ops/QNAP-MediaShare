import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api, post, type AlbumDetail, type AlbumSummary } from "../api";
import { Loading, Modal, useToast } from "../ui";
import { date, thumbUrl } from "../util";

export default function Albums() {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ["albums", "all"], queryFn: () => api<{ albums: AlbumSummary[] }>("/api/albums") });
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const create = useMutation({
    mutationFn: () => post<AlbumDetail>("/api/albums", { title: title.trim() }),
    onSuccess: (a) => {
      qc.invalidateQueries({ queryKey: ["albums"] });
      navigate(`/albums/${a.id}`);
    },
    onError: (e) => toast(String(e instanceof Error ? e.message : e), true),
  });

  const mine = q.data?.albums.filter((a) => a.role === "owner") ?? [];
  const shared = q.data?.albums.filter((a) => a.role !== "owner") ?? [];

  return (
    <>
      <div className="page-head">
        <h1>Albums</h1>
        <span className="muted">Hand-picked collections. Photos stay where they are; albums just point to them.</span>
        <div className="spacer" />
        <button className="btn primary" onClick={() => setCreating(true)}>New album</button>
      </div>
      {q.isLoading ? (
        <Loading />
      ) : !q.data?.albums.length ? (
        <div className="card empty">
          <div className="big">📚</div>
          <h2>No albums yet</h2>
          <p>
            Create one here, or select photos in <Link to="/browse">Browse</Link> and choose <strong>Add to album</strong>. In the
            viewer, press <kbd>A</kbd>.
          </p>
        </div>
      ) : (
        <>
          <AlbumGrid albums={mine} />
          {shared.length > 0 && (
            <>
              <h2 style={{ marginTop: 28 }}>Shared with me</h2>
              <AlbumGrid albums={shared} />
            </>
          )}
        </>
      )}
      {creating && (
        <Modal
          title="New album"
          onClose={() => setCreating(false)}
          footer={
            <>
              <button className="btn" onClick={() => setCreating(false)}>Cancel</button>
              <button className="btn primary" disabled={!title.trim() || create.isPending} onClick={() => create.mutate()}>Create</button>
            </>
          }
        >
          <form onSubmit={(e) => { e.preventDefault(); if (title.trim()) create.mutate(); }}>
            <label className="field">
              Name
              <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus placeholder="e.g. Summer 2024" />
            </label>
          </form>
        </Modal>
      )}
    </>
  );
}

function AlbumGrid({ albums }: { albums: AlbumSummary[] }) {
  if (!albums.length) return <p className="muted">None yet.</p>;
  return (
    <div className="album-grid">
      {albums.map((a) => (
        <Link key={a.id} to={`/albums/${a.id}`} className="album-card">
          <div className="album-cover">
            {a.cover_file_id ? <img src={thumbUrl(a.cover_file_id, "l")} alt="" loading="lazy" /> : <span>📷</span>}
          </div>
          <div className="album-meta">
            <strong>{a.title}</strong>
            <span className="small muted">
              {a.count} {a.count === 1 ? "item" : "items"} · {a.role === "owner" ? date(a.updated_at, false) : `by ${a.owner}`}
            </span>
            <span className="row" style={{ gap: 4, marginTop: 4 }}>
              {a.role === "editor" && <span className="badge accent">Can edit</span>}
              {a.role === "viewer" && <span className="badge">View only</span>}
              {a.shared_with > 0 && a.role === "owner" && <span className="badge">Shared with {a.shared_with}</span>}
              {a.active_links > 0 && <span className="badge keep">🔗 Public link</span>}
            </span>
          </div>
        </Link>
      ))}
    </div>
  );
}
