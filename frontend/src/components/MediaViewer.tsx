/**
 * Full-screen in-app viewer for photos and videos.
 *
 * Opened from anywhere via `useViewer().open(entries, startIndex)`. Photos show the 2048px preview
 * instantly, then swap in the full-resolution rendition (/api/files/:id/full; HEIC/TIFF/RAW are
 * converted server-side). Videos play through the browser-safe preview. Nothing is downloaded unless
 * the user explicitly presses Download.
 *
 * Touch: double-tap or pinch to zoom, drag to pan when zoomed, swipe left/right for next/previous, swipe down
 * to close, single tap to hide/show the controls, swipe up from the bottom to show the filmstrip. Faces: toggle with the Faces button (P); tap a face to name it or correct it.
 */
import { createContext, Fragment, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, post, type FileInfo, type ItemFace, type ItemInfo } from "../api";
import { internalUrls, MediaUrlsContext, useMediaUrls, type MediaUrls } from "../media-urls";
import { Spinner, useMediaQuery, useToast } from "../ui";
import { bytes, camera, cls, date, dims, duration, STATUS_LABEL } from "../util";
import AlbumPicker from "./AlbumPicker";
import { VideoPlayer } from "./MediaView";

/** One thing to show. `file` (the primary file id) lets the filmstrip draw thumbnails without extra requests. */
export interface ViewerEntry {
  item: number;
  file?: number | null;
  /** Pre-loaded details (public share pages); otherwise fetched from /api/items/:id. */
  info?: ItemInfo;
}

export interface ViewerOptions {
  /** Where media is loaded from (share-link URLs on public pages). */
  urls?: MediaUrls;
  /** Public share page: no album/face actions, minimal metadata. */
  publicMode?: boolean;
}

interface ViewerApi {
  open: (entries: ViewerEntry[], start?: number, opts?: ViewerOptions) => void;
  isOpen: boolean;
}

const ViewerCtx = createContext<ViewerApi>({ open: () => {}, isOpen: false });
export const useViewer = () => useContext(ViewerCtx);

export function ViewerProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ entries: ViewerEntry[]; index: number; opts: ViewerOptions } | null>(null);
  const open = useCallback((entries: ViewerEntry[], start = 0, opts: ViewerOptions = {}) => {
    if (entries.length) setState({ entries, index: Math.min(Math.max(start, 0), entries.length - 1), opts });
  }, []);
  const value = useMemo(() => ({ open, isOpen: state !== null }), [open, state]);
  return (
    <ViewerCtx.Provider value={value}>
      {children}
      {state && (
        <MediaUrlsContext.Provider value={state.opts.urls ?? internalUrls}>
          <MediaViewer
            entries={state.entries}
            index={state.index}
            publicMode={!!state.opts.publicMode}
            onIndex={(index) => setState((s) => (s ? { ...s, index } : s))}
            onClose={() => setState(null)}
          />
        </MediaUrlsContext.Provider>
      )}
    </ViewerCtx.Provider>
  );
}

const fetchItem = (id: number) => api<ItemInfo>(`/api/items/${id}`);
const fetchFaces = (id: number) => api<ItemFace[]>(`/api/items/${id}/faces`);

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
  publicMode,
  onIndex,
  onClose,
}: {
  entries: ViewerEntry[];
  index: number;
  publicMode: boolean;
  onIndex: (i: number) => void;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const urls = useMediaUrls();
  const entry = entries[index];
  const q = useQuery({ queryKey: ["item", entry.item], queryFn: () => fetchItem(entry.item), enabled: !entry.info });
  const it = entry.info ?? q.data;
  const f = it?.primary;
  const [showInfo, setShowInfo] = useState(() => readPref(publicMode ? "viewer.info.public" : "viewer.info", !publicMode));
  const [showFaces, setShowFaces] = useState(() => !publicMode && readPref("viewer.faces", false));
  const [live, setLive] = useState(false);
  const [picker, setPicker] = useState(false);
  const [tagging, setTagging] = useState<ItemFace | null>(null);
  const [zoomed, setZoomed] = useState(false);
  const [drag, setDrag] = useState<{ dx: number; dy: number } | null>(null);
  // Touch screens: controls float over the photo (tap to hide/show); the filmstrip is opt-in.
  const touch = useMediaQuery("(pointer: coarse)");
  const [stripPref, setStripPref] = useState(() => readPref("viewer.strip", false));
  const [chrome, setChrome] = useState(true);
  const setStrip = useCallback((v: boolean) => {
    setStripPref(v);
    writePref("viewer.strip", v);
  }, []);
  const canFullscreen = typeof document !== "undefined" && !!document.fullscreenEnabled;
  const zoom = useRef<ZoomApi | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const swipe = useRef<{
    x: number; y: number; id: number; bottom: boolean;
    axis: "h" | "v" | null; lx: number; lt: number; vx: number;
  } | null>(null);
  // Carousel: after a horizontal swipe the track glides to the neighbour (or back), then the index changes.
  const [settle, setSettle] = useState<{ dir: -1 | 0 | 1 } | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const settleTimer = useRef<number | undefined>(undefined);
  const GAP = 16;
  const many = entries.length > 1;
  const stripOn = many && (!touch || stripPref);
  const download = f && f.status !== "purged" ? urls.download(f.id) : null;
  const facesQ = useQuery({
    queryKey: ["item-faces", entry.item],
    queryFn: () => fetchFaces(entry.item),
    enabled: !publicMode && (showFaces || showInfo) && f?.kind === "image",
  });

  const go = useCallback(
    (delta: number) => {
      const n = index + delta;
      if (n >= 0 && n < entries.length) onIndex(n);
    },
    [index, entries.length, onIndex],
  );

  useEffect(() => {
    setLive(false);
    setTagging(null);
    setZoomed(false);
  }, [entry.item]);

  // Prefetch neighbours' details and previews so stepping through feels instant.
  useEffect(() => {
    for (const d of [-1, 1, 2]) {
      const e = entries[index + d];
      if (!e) continue;
      if (!e.info) qc.prefetchQuery({ queryKey: ["item", e.item], queryFn: () => fetchItem(e.item) });
      if (e.file) new Image().src = urls.thumb(e.file, "l");
    }
  }, [index, entries, qc, urls]);

  const toggleInfo = useCallback(() => {
    setShowInfo((v) => {
      writePref(publicMode ? "viewer.info.public" : "viewer.info", !v);
      return !v;
    });
  }, [publicMode]);
  const toggleFaces = useCallback(() => {
    setShowFaces((v) => {
      writePref("viewer.faces", !v);
      return !v;
    });
    setTagging(null);
  }, []);

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else root.current?.requestFullscreen?.().catch(() => {});
  }, []);

  // Keyboard: captured before page-level shortcuts (e.g. Review's K/D/Enter) and never leaks to them.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (picker || ["INPUT", "SELECT", "TEXTAREA"].includes(t.tagName)) return; // let dialogs handle keys
      e.stopImmediatePropagation();
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key;
      if (k === "Escape") {
        if (tagging) setTagging(null);
        else if (document.fullscreenElement) return; // browser exits fullscreen first
        else onClose();
      } else if (k === "ArrowRight") go(1);
      else if (k === "ArrowLeft") go(-1);
      else if (k === "Home") onIndex(0);
      else if (k === "End") onIndex(entries.length - 1);
      else if (k === "i" || k === "I") toggleInfo();
      else if ((k === "p" || k === "P") && !publicMode) toggleFaces();
      else if (k === "f" || k === "F") toggleFullscreen();
      else if (k === "+" || k === "=") zoom.current?.zoomBy(1.5);
      else if (k === "-" || k === "_") zoom.current?.zoomBy(1 / 1.5);
      else if (k === "0") zoom.current?.reset();
      else if ((k === "l" || k === "L") && it?.is_live_photo) setLive((v) => !v);
      else if ((k === "a" || k === "A") && !publicMode) setPicker(true);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [go, onClose, onIndex, entries.length, toggleInfo, toggleFaces, toggleFullscreen, it?.is_live_photo, picker, publicMode, tagging]);

  // Lock page scroll underneath.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  // Touch swipes on the stage (only when not zoomed in): left/right = next/previous, down = close.
  // A second finger turns the gesture into a pinch (handled by ZoomImage): never a swipe.
  const fingers = useRef<Set<number>>(new Set());
  const multiTouch = useRef(false);
  const onStageDown = (e: React.PointerEvent) => {
    if (e.pointerType !== "touch") return;
    fingers.current.add(e.pointerId);
    if (fingers.current.size > 1) {
      multiTouch.current = true;
      swipe.current = null;
      setDrag(null);
      return;
    }
    if (zoomed || tagging) return;
    // Gestures that start near the bottom edge control the filmstrip instead of closing.
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const bottom = touch && many && e.clientY > r.bottom - Math.max(110, r.height * 0.22);
    if (settle) return; // still gliding from the last swipe
    swipe.current = { x: e.clientX, y: e.clientY, id: e.pointerId, bottom, axis: null, lx: e.clientX, lt: performance.now(), vx: 0 };
  };
  const releaseFinger = (e: React.PointerEvent) => {
    fingers.current.delete(e.pointerId);
    if (fingers.current.size === 0) multiTouch.current = false;
  };
  const onStageMove = (e: React.PointerEvent) => {
    const s = swipe.current;
    if (!s || s.id !== e.pointerId || multiTouch.current) return;
    const dx = e.clientX - s.x;
    const dy = e.clientY - s.y;
    // Lock to one direction once the finger has clearly moved, like native photo apps.
    if (!s.axis && (Math.abs(dx) > 8 || Math.abs(dy) > 8)) s.axis = Math.abs(dx) > Math.abs(dy) ? "h" : "v";
    const now = performance.now();
    const dt = now - s.lt;
    if (dt > 0) s.vx = 0.7 * ((e.clientX - s.lx) / dt) + 0.3 * s.vx; // px/ms, smoothed
    s.lx = e.clientX;
    s.lt = now;
    if (s.axis === "h") {
      // Rubber-band past the first/last item.
      const edge = (dx > 0 && index === 0) || (dx < 0 && index === entries.length - 1);
      setDrag({ dx: edge ? dx * 0.3 : dx, dy: 0 });
    } else if (s.axis === "v") setDrag({ dx: 0, dy: !s.bottom && dy > 0 ? dy : 0 });
  };
  const finishSettle = useCallback(
    (dir: -1 | 0 | 1) => {
      window.clearTimeout(settleTimer.current);
      if (dir) go(dir);
      setSettle(null);
      setDrag(null);
    },
    [go],
  );
  useEffect(() => () => window.clearTimeout(settleTimer.current), []);
  const onStageUp = (e: React.PointerEvent) => {
    const wasMulti = multiTouch.current;
    if (e.pointerType === "touch") releaseFinger(e);
    const s = swipe.current;
    swipe.current = null;
    if (!s || s.id !== e.pointerId || wasMulti) {
      if (!settle) setDrag(null);
      return;
    }
    const dx = e.clientX - s.x;
    const dy = e.clientY - s.y;
    if (s.axis === "h") {
      const w = stageRef.current?.clientWidth ?? window.innerWidth;
      const want: -1 | 1 = dx < 0 ? 1 : -1;
      const flick = Math.abs(s.vx) > 0.35 && Math.sign(s.vx) === Math.sign(dx);
      const far = Math.abs(dx) > w * 0.25 && !(Math.abs(s.vx) > 0.2 && Math.sign(s.vx) !== Math.sign(dx));
      const exists = index + want >= 0 && index + want < entries.length;
      const dir: -1 | 0 | 1 = exists && Math.abs(dx) > 20 && (flick || far) ? want : 0;
      setSettle({ dir });
      settleTimer.current = window.setTimeout(() => finishSettle(dir), 400); // in case transitionend is missed
      return;
    }
    setDrag(null);
    const vertical = s.axis === "v";
    if (s.bottom && vertical && dy < -40) {
      setStrip(true);
      setChrome(true);
    } else if (s.bottom && vertical && dy > 40) setStrip(false);
    else if (!s.bottom && dy > 90 && vertical) onClose();
  };

  const tapFingers = useRef<Set<number>>(new Set());
  const tap = useRef<{ x: number; y: number; t: number; id: number } | null>(null);
  const tapTimer = useRef<number | undefined>(undefined);
  const tapPending = useRef(false);
  useEffect(() => () => window.clearTimeout(tapTimer.current), []);
  const onTapDown = (e: React.PointerEvent) => {
    if (e.pointerType !== "touch") return;
    tapFingers.current.add(e.pointerId);
    if (tapPending.current) {
      // Second tap of a double-tap: that's a zoom, not a toggle.
      window.clearTimeout(tapTimer.current);
      tapPending.current = false;
      tap.current = null;
      return;
    }
    tap.current = tapFingers.current.size === 1 ? { x: e.clientX, y: e.clientY, t: Date.now(), id: e.pointerId } : null;
  };
  const onTapUp = (e: React.PointerEvent) => {
    if (e.pointerType !== "touch") return;
    tapFingers.current.delete(e.pointerId);
    const t = tap.current;
    tap.current = null;
    if (!t || t.id !== e.pointerId || tapFingers.current.size) return;
    if (Date.now() - t.t > 300 || Math.hypot(e.clientX - t.x, e.clientY - t.y) > 10) return;
    const el = document.elementFromPoint(e.clientX, e.clientY);
    if (el?.closest("button, a, input, video[controls], .face-tagger, .vctl")) return;
    tapPending.current = true;
    tapTimer.current = window.setTimeout(() => {
      tapPending.current = false;
      setChrome((c) => !c);
    }, 300);
  };

  const faces = showFaces && f?.kind === "image" && !live ? facesQ.data ?? [] : [];

  const trackStyle: React.CSSProperties | undefined = settle
    ? {
        transform: `translate3d(${settle.dir ? -settle.dir * ((stageRef.current?.clientWidth ?? window.innerWidth) + GAP) : 0}px, 0, 0)`,
        transition: "transform 280ms cubic-bezier(0.2, 0.8, 0.2, 1)",
      }
    : drag
      ? {
          transform: `translate3d(${drag.dx}px, ${drag.dy * 0.6}px, 0)`,
          opacity: drag.dy ? Math.max(0.4, 1 - drag.dy / 400) : 1,
          transition: "none",
        }
      : undefined;

  return (
    <div
      className={cls("viewer-overlay", showInfo && "with-info", touch && "touch", touch && !chrome && "chrome-off", stripOn && "strip-on")}
      ref={root}
      role="dialog"
      aria-modal="true"
      aria-label="Media viewer"
    >
      <header className="viewer-bar">
        <div className="viewer-title">
          <strong title={f?.path}>{f?.name ?? "Loading…"}</strong>
          {f && (
            <span className="viewer-sub">
              {[f.owner, f.taken_at ? date(f.taken_at, false) : null, f.kind === "video" ? duration(f.duration) : dims(f)]
                .filter(Boolean)
                .join(" · ")}
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
              ◉ <span className="vlabel">Live</span>
            </button>
          )}
          {!publicMode && f?.kind === "image" && (
            <button className={cls("vbtn", showFaces && "on")} onClick={toggleFaces} title="Show and tag faces (P)">
              ☺ <span className="vlabel">Faces</span>
            </button>
          )}
          {!publicMode && f && (
            <button className="vbtn" onClick={() => setPicker(true)} title="Add to an album (A)">
              ＋ <span className="vlabel">Album</span>
            </button>
          )}
          <button className={cls("vbtn", showInfo && "on")} onClick={toggleInfo} title="Details (I)">
            ⓘ <span className="vlabel">Info</span>
          </button>
          {(!touch || canFullscreen) && (
            <button className="vbtn" onClick={toggleFullscreen} title="Full screen (F)" aria-label="Full screen">
              ⛶
            </button>
          )}
          {download && (
            <a className="vbtn" href={download} title="Download the original file to this computer">
              ⤓ <span className="vlabel">Download</span>
            </a>
          )}
          <button className="vbtn" onClick={onClose} title="Close (Esc)" aria-label="Close">
            ✕
          </button>
        </div>
      </header>

      <div
        className="viewer-stage"
        onPointerDownCapture={onTapDown}
        onPointerUpCapture={onTapUp}
        onPointerCancelCapture={(e) => {
          tapFingers.current.delete(e.pointerId);
          tap.current = null;
        }}
        onPointerDown={onStageDown}
        onPointerMove={onStageMove}
        onPointerUp={onStageUp}
        onPointerCancel={(e) => {
          if (e.pointerType === "touch") releaseFinger(e);
          swipe.current = null;
          if (!settle) setDrag(null);
        }}
        ref={stageRef}
      >
        <div
          className="viewer-track"
          style={trackStyle}
          onTransitionEnd={(e) => {
            if (e.target === e.currentTarget && settle) finishSettle(settle.dir);
          }}
        >
        {!f ? (
          <Spinner />
        ) : f.status === "purged" ? (
          <div className="viewer-msg">This file was permanently deleted.</div>
        ) : live && it?.companion ? (
          <VideoPlayer key={`live-${it.companion.id}`} file={it.companion} autoPlay loop customControls={touch} />
        ) : f.kind === "video" ? (
          <VideoPlayer key={f.id} file={f} autoPlay customControls={touch} />
        ) : (
          <ZoomImage
            key={f.id}
            file={f}
            apiRef={zoom}
            faces={faces}
            activeFace={tagging?.face_id ?? null}
            onFace={(face) => setTagging(face)}
            onZoomChange={setZoomed}
          />
        )}
        {touch && many && (drag?.dx || settle) ? (
          <>
            <Peek entry={entries[index - 1]} side="prev" gap={GAP} />
            <Peek entry={entries[index + 1]} side="next" gap={GAP} />
          </>
        ) : null}
        </div>
        {many && index > 0 && (
          <button className="viewer-nav prev hide-touch" onClick={() => go(-1)} aria-label="Previous (←)">
            ‹
          </button>
        )}
        {many && index < entries.length - 1 && (
          <button className="viewer-nav next hide-touch" onClick={() => go(1)} aria-label="Next (→)">
            ›
          </button>
        )}
        {tagging && it && <FaceTagger face={tagging} itemId={it.id} onDone={() => setTagging(null)} />}
      </div>

      {showInfo && <InfoPanel it={it} faces={facesQ.data} onClose={onClose} publicMode={publicMode} />}

      {stripOn && <Filmstrip entries={entries} index={index} onIndex={onIndex} onHide={touch ? () => setStrip(false) : undefined} />}
      {touch && many && !stripOn && (
        <button className="strip-handle" onClick={() => setStrip(true)} aria-label="Show filmstrip (or swipe up from the bottom)">
          <span />
        </button>
      )}
      {picker && it && <AlbumPicker itemIds={[it.id]} onClose={() => setPicker(false)} z={3500} />}
    </div>
  );
}

/** The neighbouring photo, shown beside the current one while swiping. */
function Peek({ entry, side, gap }: { entry?: ViewerEntry; side: "prev" | "next"; gap: number }) {
  const urls = useMediaUrls();
  if (!entry?.file) return null;
  return (
    <img
      className="viewer-peek"
      style={side === "prev" ? { left: `calc(-100% - ${gap}px)` } : { left: `calc(100% + ${gap}px)` }}
      src={urls.thumb(entry.file, "l")}
      alt=""
      draggable={false}
    />
  );
}

/** Pointer capture keeps a gesture on the photo even if a finger slides off it. Never let it break the gesture. */
function capture(e: React.PointerEvent) {
  try {
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  } catch {
    /* pointer already gone */
  }
}

/** Image with wheel/drag/double-click (double-tap) zoom, plus an optional face overlay that zooms with it. */
function ZoomImage({
  file,
  apiRef,
  faces,
  activeFace,
  onFace,
  onZoomChange,
}: {
  file: FileInfo;
  apiRef: React.MutableRefObject<ZoomApi | null>;
  faces: ItemFace[];
  activeFace: number | null;
  onFace: (f: ItemFace) => void;
  onZoomChange: (zoomed: boolean) => void;
}) {
  const urls = useMediaUrls();
  const [src, setSrc] = useState<string | null>(file.thumb ? urls.thumb(file.id, "l") : null);
  const [quality, setQuality] = useState<"preview" | "full" | "failed">("preview");
  const [view, setView] = useState({ s: 1, x: 0, y: 0 });
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const [boxSize, setBoxSize] = useState<{ w: number; h: number }>({ w: 0, h: 0 });
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef<{ px: number; py: number; x: number; y: number; id: number } | null>(null);
  const lastTap = useRef<{ t: number; x: number; y: number } | null>(null);
  const touchToggled = useRef(0); // browsers may also fire dblclick after a double-tap
  const touches = useRef<Map<number, { x: number; y: number }>>(new Map());
  const pinch = useRef<{ d0: number; mx0: number; my0: number; s0: number; x0: number; y0: number } | null>(null);
  const [showPct, setShowPct] = useState(false);
  const firstScale = useRef(true);

  useEffect(() => {
    let alive = true;
    const img = new Image();
    img.onload = () => {
      if (!alive) return;
      setSrc(img.src);
      setQuality("full");
    };
    img.onerror = () => alive && setQuality("failed");
    img.src = urls.full(file.id);
    return () => {
      alive = false;
      img.onload = img.onerror = null;
    };
  }, [file.id, urls]);

  useEffect(() => onZoomChange(view.s > 1), [view.s, onZoomChange]);
  // The zoom level only shows while it's changing.
  useEffect(() => {
    if (firstScale.current) {
      firstScale.current = false;
      return;
    }
    setShowPct(true);
    const t = window.setTimeout(() => setShowPct(false), 900);
    return () => window.clearTimeout(t);
  }, [view.s]);

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const update = () => setBoxSize({ w: el.clientWidth, h: el.clientHeight });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Zoom keeping the point under (cx, cy) - coords relative to the box centre - fixed on screen.
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

  const endPointer = (id: number) => {
    touches.current.delete(id);
    if (drag.current?.id === id) drag.current = null;
    if (pinch.current && touches.current.size < 2) {
      pinch.current = null;
      setView((v) => {
        if (v.s <= 1.02) return { s: 1, x: 0, y: 0 }; // pinched back out: snap to fit
        // One finger still down: carry on panning with it.
        const rest = [...touches.current.entries()][0];
        if (rest) drag.current = { px: rest[1].x, py: rest[1].y, x: v.x, y: v.y, id: rest[0] };
        return v;
      });
    }
  };

  const toggleZoomAt = (clientX: number, clientY: number) => {
    if (view.s > 1) setView({ s: 1, x: 0, y: 0 });
    else zoomAt(2.5, ...rel(clientX, clientY));
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

  // Fit the image inside the box (like object-fit: contain) so faces can be positioned in % of it.
  const frame = useMemo(() => {
    if (!natural || !boxSize.w || !boxSize.h) return null;
    const k = Math.min(boxSize.w / natural.w, boxSize.h / natural.h);
    const w = natural.w * k;
    const h = natural.h * k;
    return { w, h, left: (boxSize.w - w) / 2, top: (boxSize.h - h) / 2 };
  }, [natural, boxSize]);

  if (!src && quality === "failed") return <div className="viewer-msg">This image can't be displayed ({file.ext.toUpperCase()}).</div>;

  return (
    <div
      ref={box}
      className={cls("zoom-box", view.s > 1 && "zoomed", drag.current && "dragging")}
      onDoubleClick={(e) => {
        if (Date.now() - touchToggled.current > 600) toggleZoomAt(e.clientX, e.clientY);
      }}
      onPointerDown={(e) => {
        if (e.pointerType === "touch") {
          touches.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
          capture(e);
          if (touches.current.size === 2) {
            // Second finger: start a pinch (the stage sees it too and cancels any swipe).
            const [a, b] = [...touches.current.values()];
            const [mx, my] = rel((a.x + b.x) / 2, (a.y + b.y) / 2);
            pinch.current = { d0: Math.hypot(a.x - b.x, a.y - b.y) || 1, mx0: mx, my0: my, s0: view.s, x0: view.x, y0: view.y };
            drag.current = null;
            lastTap.current = null;
            return;
          }
          if (touches.current.size > 2) return;
          const now = Date.now();
          const lt = lastTap.current;
          if (lt && now - lt.t < 320 && Math.hypot(e.clientX - lt.x, e.clientY - lt.y) < 40) {
            lastTap.current = null;
            touchToggled.current = now;
            toggleZoomAt(e.clientX, e.clientY); // double-tap
            e.stopPropagation();
            return;
          }
          lastTap.current = { t: now, x: e.clientX, y: e.clientY };
        }
        if (view.s === 1 || (e.pointerType === "mouse" && e.button !== 0)) return;
        e.stopPropagation(); // panning a zoomed photo is not a swipe
        capture(e);
        drag.current = { px: e.clientX, py: e.clientY, x: view.x, y: view.y, id: e.pointerId };
      }}
      onPointerMove={(e) => {
        if (e.pointerType === "touch" && touches.current.has(e.pointerId)) {
          touches.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
          const pz = pinch.current;
          if (pz && touches.current.size >= 2) {
            const [a, b] = [...touches.current.values()];
            const [mx, my] = rel((a.x + b.x) / 2, (a.y + b.y) / 2);
            const s = Math.min(8, Math.max(1, (pz.s0 * Math.hypot(a.x - b.x, a.y - b.y)) / pz.d0));
            const k = s / pz.s0;
            // Keep the photo point that was under the fingers' midpoint under the midpoint now.
            setView({ s, x: mx - (pz.mx0 - pz.x0) * k, y: my - (pz.my0 - pz.y0) * k });
            return;
          }
        }
        const d = drag.current;
        if (d && d.id === e.pointerId) setView((v) => ({ ...v, x: d.x + e.clientX - d.px, y: d.y + e.clientY - d.py }));
      }}
      onPointerUp={(e) => endPointer(e.pointerId)}
      onPointerCancel={(e) => endPointer(e.pointerId)}
    >
      {src ? (
        <div
          className="zoom-frame"
          style={
            frame
              ? { left: frame.left, top: frame.top, width: frame.w, height: frame.h, transform: `translate(${view.x}px, ${view.y}px) scale(${view.s})` }
              : { visibility: "hidden" }
          }
        >
          <img
            src={src}
            alt={file.name}
            draggable={false}
            onLoad={(e) => setNatural({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
          />
          {faces.map((fc) => {
            const [x1, y1, x2, y2] = fc.bbox;
            const label = fc.name ?? (fc.suggested_name ? `${fc.suggested_name}?` : "Who?");
            return (
              <button
                key={fc.face_id}
                className={cls("face-box", !fc.name && "unknown", activeFace === fc.face_id && "active")}
                style={{ left: `${x1 * 100}%`, top: `${y1 * 100}%`, width: `${(x2 - x1) * 100}%`, height: `${(y2 - y1) * 100}%` }}
                onPointerDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  onFace(fc);
                }}
                aria-label={fc.name ? `Face: ${fc.name}` : "Unknown face, tap to name"}
              >
                <span className="face-label" style={{ transform: `scale(${1 / view.s})` }}>{label}</span>
              </button>
            );
          })}
        </div>
      ) : (
        <Spinner />
      )}
      <div className={cls("zoom-pct", showPct && "on")} aria-live="polite">
        {Math.round(view.s * 100)}%
        {view.s > 1 && quality === "preview" && <span className="zoom-q"><Spinner /> loading full res</span>}
        {view.s > 1 && quality === "failed" && <span className="zoom-q">preview only</span>}
      </div>
      {view.s > 1 && (
        <button
          className="zoom-reset"
          onClick={() => setView({ s: 1, x: 0, y: 0 })}
          onPointerDown={(e) => e.stopPropagation()}
          onDoubleClick={(e) => e.stopPropagation()}
          title="Reset zoom (0)"
          aria-label="Reset zoom"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" />
          </svg>
        </button>
      )}
    </div>
  );
}

/** Popover to name/correct a face (shared family list; every change is audited server-side). */
function FaceTagger({ face, itemId, onDone }: { face: ItemFace; itemId: number; onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState("");
  const names = useQuery({ queryKey: ["people-names"], queryFn: () => api<{ id: number; name: string }[]>("/api/people/names") });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["item-faces", itemId] });
    qc.invalidateQueries({ queryKey: ["people"] });
    qc.invalidateQueries({ queryKey: ["people-names"] });
    qc.invalidateQueries({ queryKey: ["person"] });
  };
  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body: unknown }) => post(path, body),
    onSuccess: () => {
      refresh();
      onDone();
    },
    onError: (e) => toast(String(e instanceof Error ? e.message : e), true),
  });
  const ids = { face_ids: [face.face_id] };
  const assign = (n: string) => n.trim() && act.mutate({ path: "/api/faces/assign", body: { ...ids, name: n.trim() } });

  return (
    <div className="face-tagger" onPointerDown={(e) => e.stopPropagation()} role="dialog" aria-label="Name this face">
      {face.name ? (
        <div className="row" style={{ marginBottom: 8 }}>
          <strong className="grow">{face.name}</strong>
          <button className="vbtn" onClick={() => act.mutate({ path: "/api/faces/unassign", body: ids })}>Not {face.name}</button>
        </div>
      ) : face.suggested_name ? (
        <div className="row" style={{ marginBottom: 8 }}>
          <span className="grow">Is this <strong>{face.suggested_name}</strong>?</span>
          <button className="vbtn on" onClick={() => act.mutate({ path: "/api/faces/confirm", body: ids })}>Yes</button>
          <button className="vbtn" onClick={() => act.mutate({ path: "/api/faces/unassign", body: ids })}>No</button>
        </div>
      ) : null}
      <form className="row" style={{ flexWrap: "nowrap" }} onSubmit={(e) => { e.preventDefault(); assign(name); }}>
        <input
          type="text"
          list="face-names"
          placeholder={face.name ? "Someone else…" : "Who is this?"}
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
          className="grow"
          aria-label="Name"
        />
        <datalist id="face-names">
          {names.data?.map((p) => <option key={p.id} value={p.name} />)}
        </datalist>
        <button className="vbtn on" disabled={!name.trim() || act.isPending}>Save</button>
      </form>
      <div className="row" style={{ marginTop: 8, justifyContent: "space-between" }}>
        <button className="vbtn" onClick={() => act.mutate({ path: "/api/faces/ignore", body: ids })} title="Remove this detection">Not a face</button>
        <button className="vbtn" onClick={onDone}>Cancel</button>
      </div>
    </div>
  );
}

function InfoPanel({
  it,
  faces,
  onClose,
  publicMode,
}: {
  it: ItemInfo | undefined;
  faces: ItemFace[] | undefined;
  onClose: () => void;
  publicMode: boolean;
}) {
  if (!it) return <aside className="viewer-info" />;
  const f = it.primary;
  if (publicMode) {
    return (
      <aside className="viewer-info">
        <dl className="kv">
          <dt>Taken</dt><dd>{date(f.taken_at)}</dd>
          <dt>Camera</dt><dd>{camera(f)}</dd>
          <dt>Dimensions</dt><dd>{dims(f)}</dd>
          {f.kind === "video" && (<><dt>Duration</dt><dd>{duration(f.duration)}</dd></>)}
          <dt>Format / size</dt><dd>{f.ext.toUpperCase()} · {bytes(f.size)}</dd>
          {it.is_live_photo && (<><dt>Live Photo</dt><dd>Press L or ◉ Live to play the motion</dd></>)}
        </dl>
        <div className="viewer-keys">
          <kbd>←</kbd><kbd>→</kbd> browse · <kbd>+</kbd><kbd>−</kbd><kbd>0</kbd> zoom · <kbd>F</kbd> full screen · <kbd>I</kbd> info · <kbd>Esc</kbd> close
        </div>
      </aside>
    );
  }
  const people = Array.from(
    new Map((faces ?? []).filter((x) => x.person_id && x.name).map((x) => [x.person_id!, x.name!])).entries(),
  );
  const unknown = (faces ?? []).filter((x) => !x.name).length;
  return (
    <aside className="viewer-info">
      {(people.length > 0 || unknown > 0) && (
        <div className="row" style={{ marginBottom: 12 }}>
          <span className="muted small">People:</span>
          {people.map(([pid, name]) => (
            <Link key={pid} to={`/people/${pid}`} className="badge accent" onClick={onClose}>{name}</Link>
          ))}
          {unknown > 0 && <span className="badge">{unknown} unnamed</span>}
        </div>
      )}
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
      <ItemAlbums itemId={it.id} onClose={onClose} />
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
      <div className="viewer-keys hide-touch">
        <kbd>←</kbd><kbd>→</kbd> browse · <kbd>+</kbd><kbd>−</kbd><kbd>0</kbd> zoom · <kbd>P</kbd> faces · <kbd>A</kbd> add to album · <kbd>F</kbd> full screen · <kbd>I</kbd> info · <kbd>Esc</kbd> close
      </div>
      <div className="viewer-keys show-touch">Pinch or double-tap to zoom · swipe for next · swipe down to close</div>
    </aside>
  );
}

function ItemAlbums({ itemId, onClose }: { itemId: number; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["item-albums", itemId],
    queryFn: () => api<{ albums: { id: number; title: string }[] }>(`/api/albums/for-item/${itemId}`),
  });
  if (!q.data?.albums.length) return null;
  return (
    <div className="row" style={{ marginTop: 12 }}>
      <span className="muted small">In albums:</span>
      {q.data.albums.map((a) => (
        <Link key={a.id} to={`/albums/${a.id}`} className="badge accent" onClick={onClose}>
          {a.title}
        </Link>
      ))}
    </div>
  );
}

function Filmstrip({
  entries,
  index,
  onIndex,
  onHide,
}: {
  entries: ViewerEntry[];
  index: number;
  onIndex: (i: number) => void;
  onHide?: () => void;
}) {
  const urls = useMediaUrls();
  const strip = useRef<HTMLDivElement>(null);
  const start = useRef<{ x: number; y: number } | null>(null);
  useEffect(() => {
    strip.current?.querySelector<HTMLElement>(".on")?.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
  }, [index]);
  // Render a window around the current position so long lists stay cheap.
  const from = Math.max(0, index - 40);
  const to = Math.min(entries.length, index + 41);
  return (
    <div
      className="viewer-strip"
      ref={strip}
      onPointerDown={(e) => {
        if (onHide && e.pointerType === "touch") start.current = { x: e.clientX, y: e.clientY };
      }}
      onPointerUp={(e) => {
        const s0 = start.current;
        start.current = null;
        if (!s0 || !onHide) return;
        const dx = e.clientX - s0.x;
        const dy = e.clientY - s0.y;
        if (dy > 30 && dy > Math.abs(dx) * 1.3) onHide(); // swipe down on the strip hides it
      }}
      onPointerCancel={() => (start.current = null)}
    >
      {entries.slice(from, to).map((e, k) => {
        const i = from + k;
        return (
          <button key={`${e.item}-${i}`} className={cls("strip-thumb", i === index && "on")} onClick={() => onIndex(i)} aria-label={`Item ${i + 1}`}>
            {e.file ? <img src={urls.thumb(e.file)} alt="" loading="lazy" /> : <span>{i + 1}</span>}
          </button>
        );
      })}
    </div>
  );
}
