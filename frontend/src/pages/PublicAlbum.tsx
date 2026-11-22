// Read-only album page for people who received a share link. No account, no access to anything else.
import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, ApiError, post, type FileInfo, type ItemInfo, type PublicAlbum as PublicAlbumData, type PublicItem } from "../api";
import { useViewer } from "../components/MediaViewer";
import { shareUrls } from "../media-urls";
import { Pager, pageCount, Spinner, usePageParam } from "../ui";
import { date, duration } from "../util";

/** Shape a public item like the app's ItemInfo so the shared viewer can display it (no private fields). */
function toItemInfo(p: PublicItem): ItemInfo {
  const base: FileInfo = {
    id: p.file_id, item_id: p.item_id, role: "primary", path: p.name, original_path: null, name: p.name, folder: "",
    ext: p.ext, kind: p.kind, size: p.size, width: p.width, height: p.height, duration: p.duration, codec: null,
    taken_at: p.taken_at, make: null, model: p.camera, lens: null, gps_lat: null, gps_lon: null, has_gps: false,
    has_exif: false, exif_score: 0, exif: null, status: "active", owner_id: null, owner: "", thumb: p.thumb,
    process_state: "done", process_error: null, first_seen_at: null, quarantined_at: null,
  };
  const companion: FileInfo | null = p.live_file_id
    ? { ...base, id: p.live_file_id, role: "live_video", kind: "video", ext: "mov", width: null, height: null, duration: null, thumb: false }
    : null;
  return { id: p.item_id, primary: base, companion, is_live_photo: !!p.live_file_id, owner_id: null, owner: "" };
}

const PAGE_SIZE = 60;

export default function PublicAlbum() {
  const { token = "" } = useParams();
  const qc = useQueryClient();
  const viewer = useViewer();
  const [page, setPage] = usePageParam();
  const [password, setPassword] = useState("");
  const [pwError, setPwError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const q = useQuery({
    queryKey: ["public-album", token],
    queryFn: () => api<PublicAlbumData>(`/api/public/s/${encodeURIComponent(token)}`),
    retry: false,
  });
  const urls = useMemo(() => shareUrls(token, !!q.data?.allow_download), [token, q.data?.allow_download]);
  const entries = useMemo(
    () => (q.data?.items ?? []).map((p) => ({ item: p.item_id, file: p.file_id, info: toItemInfo(p) })),
    [q.data],
  );
  useEffect(() => {
    if (q.data) document.title = `${q.data.title} · ${q.data.app_name}`;
  }, [q.data]);

  const err = q.error instanceof ApiError ? q.error : null;
  const needsPassword = err?.status === 401;

  const unlock = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setPwError(null);
    try {
      await post(`/api/public/s/${encodeURIComponent(token)}/unlock`, { password });
      await qc.invalidateQueries({ queryKey: ["public-album", token] });
    } catch (x) {
      setPwError(x instanceof ApiError && x.status === 429 ? x.message : "That password isn't right.");
    } finally {
      setBusy(false);
    }
  };

  if (q.isLoading) {
    return <div className="public-center"><Spinner /></div>;
  }
  if (needsPassword) {
    return (
      <div className="public-center">
        <form className="card login" onSubmit={unlock}>
          <h2 style={{ margin: 0 }}>🔒 This album is password-protected</h2>
          <p className="muted" style={{ margin: 0 }}>Enter the password you were given.</p>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus aria-label="Password" />
          {pwError && <div className="banner error">{pwError}</div>}
          <button className="btn primary lg" disabled={!password || busy}>{busy ? "Checking…" : "Open album"}</button>
        </form>
      </div>
    );
  }
  if (!q.data) {
    return (
      <div className="public-center">
        <div className="card login" style={{ textAlign: "center" }}>
          <div style={{ fontSize: 40 }}>🔗</div>
          <h2 style={{ margin: 0 }}>Link not available</h2>
          <p className="muted" style={{ margin: 0 }}>This link doesn't exist, has expired, or was turned off by the person who shared it.</p>
        </div>
      </div>
    );
  }

  const a = q.data;
  const pages = pageCount(a.items.length, PAGE_SIZE);
  const start = (Math.min(page, pages) - 1) * PAGE_SIZE;
  const open = (i: number) =>
    viewer.open(entries, i, { urls, publicMode: true, onClose: (pos) => setPage(Math.floor(pos / PAGE_SIZE) + 1) });
  return (
    <div className="public-page">
      <header className="public-head">
        <div className="grow">
          <div className="small faint">{a.app_name} · shared album</div>
          <h1>{a.title}</h1>
          {a.description && <p className="muted" style={{ margin: "6px 0 0" }}>{a.description}</p>}
          <div className="small faint" style={{ marginTop: 6 }}>
            {a.items.length} {a.items.length === 1 ? "item" : "items"}
            {a.expires_at && ` · link available until ${date(a.expires_at, false)}`}
          </div>
        </div>
        {a.allow_download && a.items.length > 0 && (
          <a className="btn primary" href={`/api/public/s/${encodeURIComponent(token)}/download.zip`}>⤓ Download all</a>
        )}
      </header>
      {a.items.length === 0 ? (
        <div className="card empty">This album is empty.</div>
      ) : (
        <>
        <Pager top page={Math.min(page, pages)} pages={pages} onPage={setPage} />
        <div className="grid public-grid">
          {a.items.slice(start, start + PAGE_SIZE).map((p, i) => (
            <button key={p.item_id} className="tile public-tile" onClick={() => open(start + i)} aria-label={`Open ${p.name}`}>
              <div className="thumb" style={{ width: "100%", aspectRatio: "1" }}>
                {p.thumb ? <img src={urls.thumb(p.file_id)} alt="" loading="lazy" /> : <div className="ph">{p.ext.toUpperCase()}</div>}
                {(p.kind === "video" || p.live_file_id) && (
                  <div className="corner">
                    {p.kind === "video" && <span className="badge">▶ {duration(p.duration)}</span>}
                    {p.live_file_id && <span className="badge">LIVE</span>}
                  </div>
                )}
              </div>
            </button>
          ))}
        </div>
        <Pager page={Math.min(page, pages)} pages={pages} onPage={setPage} />
        </>
      )}
    </div>
  );
}
