/**
 * Full-screen in-app viewer for photos and videos.
 *
 * Opened from anywhere via `useViewer().open(entries, startIndex)`. Photos show the 2048px preview
 * instantly, then swap in the full-resolution rendition (/api/files/:id/full; HEIC/TIFF/RAW are
 * converted server-side). Videos play through the browser-safe preview. Nothing is downloaded unless
 * the user explicitly presses Download.
 */
import { createContext, Fragment, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type FileInfo, type ItemInfo } from "../api";
import { Spinner } from "../ui";
import { bytes, camera, cls, date, dims, downloadUrl, duration, fullUrl, STATUS_LABEL, thumbUrl } from "../util";
import { VideoPlayer } from "./MediaView";

/** One thing to show. `file` (the primary file id) lets the filmstrip draw thumbnails without extra requests. */
export interface ViewerEntry {
  item: number;
  file?: number | null;
}

interface ViewerApi {
  open: (entries: ViewerEntry[], start?: number) => void;
  isOpen: boolean;
}

const ViewerCtx = createContext<ViewerApi>({ open: () => {}, isOpen: false });
export const useViewer = () => useContext(ViewerCtx);

export function ViewerProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ entries: ViewerEntry[]; index: number } | null>(null);
  const open = useCallback((entries: ViewerEntry[], start = 0) => {
    if (entries.length) setState({ entries, index: Math.min(Math.max(start, 0), entries.length - 1) });
  }, []);
  const value = useMemo(() => ({ open, isOpen: state !== null }), [open, state]);
  return (
    <ViewerCtx.Provider value={value}>
      {children}
      {state && (
        <MediaViewer
          entries={state.entries}
          index={state.index}
          onIndex={(index) => setState((s) => (s ? { ...s, index } : s))}
          onClose={() => setState(null)}
        />
      )}
    </ViewerCtx.Provider>
  );
}

const fetchItem = (id: number) => api<ItemInfo>(`/api/items/${id}`);

function readPref(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v === "1";
  } catch {
    return fallback;
  }
}
function writePref(key: string, v: boolean) {
  try {
    localStorage.setItem(key, v ? "1" : "0");
  } catch {
    /* storage unavailable */
  }
}

interface ZoomApi {
  zoomBy: (factor: number) => void;
  reset: () => void;
}

function MediaViewer({
  entries,
  index,
  onIndex,
  onClose,
}: {
  entries: ViewerEntry[];
  index: number;
  onIndex: (i: number) => void;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const entry = entries[index];
  const q = useQuery({ queryKey: ["item", entry.item], queryFn: () => fetchItem(entry.item) });
  const it = q.data;
  const f = it?.primary;
  const [showInfo, setShowInfo] = useState(() => readPref("viewer.info", true));
  const [live, setLive] = useState(false);
  const zoom = useRef<ZoomApi | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const many = entries.length > 1;

  const go = useCallback(
    (delta: number) => {
      const n = index + delta;
      if (n >= 0 && n < entries.length) onIndex(n);
    },
    [index, entries.length, onIndex],
  );

  useEffect(() => setLive(false), [entry.item]);

  // Prefetch neighbours' details and previews so stepping through feels instant.
  useEffect(() => {
    for (const d of [-1, 1, 2]) {
      const e = entries[index + d];
      if (!e) continue;
      qc.prefetchQuery({ queryKey: ["item", e.item], queryFn: () => fetchItem(e.item) });
      if (e.file) new Image().src = thumbUrl(e.file, "l");
    }
  }, [index, entries, qc]);

  const toggleInfo = useCallback(() => {
    setShowInfo((v) => {
      writePref("viewer.info", !v);
      return !v;
    });
  }, []);

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else root.current?.requestFullscreen?.().catch(() => {});
  }, []);

  // Keyboard: captured before page-level shortcuts (e.g. Review's K/D/Enter) and never leaks to them.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (["INPUT", "SELECT", "TEXTAREA"].includes(t.tagName)) return;
      e.stopImmediatePropagation();
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key;
      if (k === "Escape") {
        if (document.fullscreenElement) return; // browser exits fullscreen first
        onClose();
      } else if (k === "ArrowRight") go(1);
      else if (k === "ArrowLeft") go(-1);
      else if (k === "Home") onIndex(0);
      else if (k === "End") onIndex(entries.length - 1);
      else if (k === "i" || k === "I") toggleInfo();
      else if (k === "f" || k === "F") toggleFullscreen();
      else if (k === "+" || k === "=") zoom.current?.zoomBy(1.5);
      else if (k === "-" || k === "_") zoom.current?.zoomBy(1 / 1.5);
      else if (k === "0") zoom.current?.reset();
      else if ((k === "l" || k === "L") && it?.is_live_photo) setLive((v) => !v);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [go, onClose, onIndex, entries.length, toggleInfo, toggleFullscreen, it?.is_live_photo]);

  // Lock page scroll underneath.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  return (
    <div className={cls("viewer-overlay", showInfo && "with-info")} ref={root} role="dialog" aria-modal="true" aria-label="Media viewer">
      <header className="viewer-bar">
        <div className="viewer-title">
          <strong title={f?.path}>{f?.name ?? "Loading…"}</strong>
          {f && (
            <span className="viewer-sub">
              {f.owner} · {date(f.taken_at, false)} · {f.kind === "video" ? duration(f.duration) : dims(f)}
            </span>
          )}
        </div>
        {many && (
          <span className="viewer-count">
            {index + 1} / {entries.length}
          </span>
        )}
        <div className="viewer-actions">
          {it?.is_live_photo && it.companion && (
            <button className={cls("vbtn", live && "on")} onClick={() => setLive((v) => !v)} title="Play the Live Photo motion (L)">
              ◉ Live
            </button>
          )}
          <button className={cls("vbtn", showInfo && "on")} onClick={toggleInfo} title="Details (I)">
            ⓘ Info
          </button>
          <button className="vbtn" onClick={toggleFullscreen} title="Full screen (F)">
            ⛶
          </button>
          {f && f.status !== "purged" && (
            <a className="vbtn" href={downloadUrl(f.id)} title="Download the original file to this computer">
              ⤓ Download
            </a>
          )}
          <button className="vbtn" onClick={onClose} title="Close (Esc)" aria-label="Close">
            ✕
          </button>
        </div>
      </header>

      <div className="viewer-stage">
        {!f ? (
          <Spinner />
        ) : f.status === "purged" ? (
          <div className="viewer-msg">This file was permanently deleted.</div>
        ) : live && it?.companion ? (
          <VideoPlayer key={`live-${it.companion.id}`} file={it.companion} autoPlay loop />
        ) : f.kind === "video" ? (
          <VideoPlayer key={f.id} file={f} autoPlay />
        ) : (
          <ZoomImage key={f.id} file={f} apiRef={zoom} />
        )}
        {many && index > 0 && (
          <button className="viewer-nav prev" onClick={() => go(-1)} aria-label="Previous (←)">
            ‹
          </button>
        )}
        {many && index < entries.length - 1 && (
          <button className="viewer-nav next" onClick={() => go(1)} aria-label="Next (→)">
            ›
          </button>
        )}
      </div>

      {showInfo && <InfoPanel it={it} onClose={onClose} />}

      {many && <Filmstrip entries={entries} index={index} onIndex={onIndex} />}
    </div>
  );
}

/** Image with wheel/drag/double-click zoom. Shows the 2048px preview at once, then the full-resolution version. */
function ZoomImage({ file, apiRef }: { file: FileInfo; apiRef: React.MutableRefObject<ZoomApi | null> }) {
  const [src, setSrc] = useState<string | null>(file.thumb ? thumbUrl(file.id, "l") : null);
  const [quality, setQuality] = useState<"preview" | "full" | "failed">("preview");
  const [view, setView] = useState({ s: 1, x: 0, y: 0 });
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef<{ px: number; py: number; x: number; y: number } | null>(null);

  useEffect(() => {
    let alive = true;
    const img = new Image();
    img.onload = () => {
      if (!alive) return;
      setSrc(img.src);
      setQuality("full");
    };
    img.onerror = () => alive && setQuality("failed");
    img.src = fullUrl(file.id);
    return () => {
      alive = false;
      img.onload = img.onerror = null;
    };
  }, [file.id]);

  // Zoom keeping the point under (cx, cy) — viewport coords relative to the box centre — fixed.
  const zoomAt = useCallback((factor: number, cx = 0, cy = 0) => {
    setView((v) => {
      const s = Math.min(8, Math.max(1, v.s * factor));
      if (s === 1) return { s: 1, x: 0, y: 0 };
      const r = s / v.s;
      return { s, x: cx - (cx - v.x) * r, y: cy - (cy - v.y) * r };
    });
  }, []);

  useEffect(() => {
    apiRef.current = { zoomBy: (k) => zoomAt(k), reset: () => setView({ s: 1, x: 0, y: 0 }) };
    return () => {
      apiRef.current = null;
    };
  }, [apiRef, zoomAt]);

  const rel = (clientX: number, clientY: number) => {
    const r = box.current!.getBoundingClientRect();
    return [clientX - r.left - r.width / 2, clientY - r.top - r.height / 2] as const;
  };

  // Non-passive wheel listener so the page doesn't scroll while zooming.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const [cx, cy] = rel(e.clientX, e.clientY);
      zoomAt(e.deltaY < 0 ? 1.2 : 1 / 1.2, cx, cy);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  if (!src && quality === "failed") return <div className="viewer-msg">This image can't be displayed ({file.ext.toUpperCase()}).</div>;

  return (
    <div
      ref={box}
      className={cls("zoom-box", view.s > 1 && "zoomed", drag.current && "dragging")}
      onDoubleClick={(e) => {
        if (view.s > 1) setView({ s: 1, x: 0, y: 0 });
        else zoomAt(2.5, ...rel(e.clientX, e.clientY));
      }}
      onPointerDown={(e) => {
        if (view.s === 1 || e.button !== 0) return;
        (e.target as HTMLElement).setPointerCapture(e.pointerId);
        drag.current = { px: e.clientX, py: e.clientY, x: view.x, y: view.y };
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (d) setView((v) => ({ ...v, x: d.x + e.clientX - d.px, y: d.y + e.clientY - d.py }));
      }}
      onPointerUp={() => (drag.current = null)}
      onPointerCancel={() => (drag.current = null)}
    >
      {src ? (
        <img
          src={src}
          alt={file.name}
          draggable={false}
          style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.s})` }}
        />
      ) : (
        <Spinner />
      )}
      <div className="zoom-hud" onDoubleClick={(e) => e.stopPropagation()}>
        <button className="vbtn" onClick={() => zoomAt(1 / 1.5)} aria-label="Zoom out (−)">−</button>
        <button className="vbtn zoom-pct" onClick={() => setView({ s: 1, x: 0, y: 0 })} title="Fit to screen (0)">
          {Math.round(view.s * 100)}%
        </button>
        <button className="vbtn" onClick={() => zoomAt(1.5)} aria-label="Zoom in (+)">+</button>
        <span className="zoom-q" title={quality === "full" ? "Full resolution" : quality === "failed" ? "Full resolution unavailable" : "Loading full resolution…"}>
          {quality === "full" ? "Full res" : quality === "failed" ? "Preview" : <><Spinner /> Loading full res</>}
        </span>
      </div>
    </div>
  );
}

function InfoPanel({ it, onClose }: { it: ItemInfo | undefined; onClose: () => void }) {
  if (!it) return <aside className="viewer-info" />;
  const f = it.primary;
  return (
    <aside className="viewer-info">
      <dl className="kv">
        <dt>Owner</dt><dd>{f.owner}</dd>
        <dt>Folder</dt><dd className="mono">{f.folder || "/"}</dd>
        <dt>Status</dt><dd>{STATUS_LABEL[f.status] ?? f.status}</dd>
        <dt>Taken</dt><dd>{date(f.taken_at)}</dd>
        <dt>Camera</dt><dd>{camera(f)}</dd>
        {f.lens && (<><dt>Lens</dt><dd>{f.lens}</dd></>)}
        <dt>Dimensions</dt><dd>{dims(f)}</dd>
        {f.kind === "video" && (<><dt>Duration</dt><dd>{duration(f.duration)} · {f.codec ?? "?"}</dd></>)}
        <dt>Format / size</dt><dd>{f.ext.toUpperCase()} · {bytes(f.size)}</dd>
        <dt>Location</dt>
        <dd>
          {f.has_gps ? (
            <a href={`https://www.openstreetmap.org/?mlat=${f.gps_lat}&mlon=${f.gps_lon}#map=16/${f.gps_lat}/${f.gps_lon}`} target="_blank" rel="noreferrer">
              {f.gps_lat?.toFixed(5)}, {f.gps_lon?.toFixed(5)}
            </a>
          ) : "—"}
        </dd>
        <dt>EXIF</dt><dd>{f.has_exif ? `Yes (score ${f.exif_score}/9)` : "None"}</dd>
        {it.is_live_photo && (<><dt>Live Photo</dt><dd>Paired with {it.companion?.name}</dd></>)}
        <dt>Added</dt><dd>{date(f.first_seen_at)}</dd>
      </dl>
      {it.group_ids && it.group_ids.length > 0 && (
        <div className="row" style={{ marginTop: 12 }}>
          <span className="muted small">Duplicate groups:</span>
          {it.group_ids.map((g) => (
            <Link key={g} to={`/review?group=${g}&status=all`} className="badge accent" onClick={onClose}>
              #{g}
            </Link>
          ))}
        </div>
      )}
      {f.exif && (
        <details style={{ marginTop: 12 }}>
          <summary className="muted small" style={{ cursor: "pointer" }}>All metadata</summary>
          <dl className="kv" style={{ marginTop: 8 }}>
            {Object.entries(f.exif).map(([k, v]) => (
              <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>
            ))}
          </dl>
        </details>
      )}
      <div className="viewer-keys">
        <kbd>←</kbd><kbd>→</kbd> browse · <kbd>+</kbd><kbd>−</kbd><kbd>0</kbd> zoom · <kbd>F</kbd> full screen · <kbd>I</kbd> info · <kbd>Esc</kbd> close
      </div>
    </aside>
  );
}

function Filmstrip({ entries, index, onIndex }: { entries: ViewerEntry[]; index: number; onIndex: (i: number) => void }) {
  const strip = useRef<HTMLDivElement>(null);
  useEffect(() => {
    strip.current?.querySelector<HTMLElement>(".on")?.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
  }, [index]);
  // Render a window around the current position so long lists stay cheap.
  const from = Math.max(0, index - 40);
  const to = Math.min(entries.length, index + 41);
  return (
    <div className="viewer-strip" ref={strip}>
      {entries.slice(from, to).map((e, k) => {
        const i = from + k;
        return (
          <button key={`${e.item}-${i}`} className={cls("strip-thumb", i === index && "on")} onClick={() => onIndex(i)} aria-label={`Item ${i + 1}`}>
            {e.file ? <img src={thumbUrl(e.file)} alt="" loading="lazy" /> : <span>{i + 1}</span>}
          </button>
        );
      })}
    </div>
  );
}
