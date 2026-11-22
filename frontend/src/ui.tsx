import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, DEFAULT_CONFIG, type AppConfig, type FileInfo } from "./api";
import { cls, duration, thumbUrl } from "./util";

/* ---------------- deployment config (app name, map tiles) ---------------- */

export function useAppConfig(): AppConfig {
  const q = useQuery({
    queryKey: ["app-config"],
    queryFn: () => api<AppConfig>("/api/config"),
    staleTime: Infinity,
  });
  const cfg = q.data ?? DEFAULT_CONFIG;
  useEffect(() => {
    document.title = cfg.app_name;
  }, [cfg.app_name]);
  return cfg;
}

export function Brand({ compact }: { compact?: boolean }) {
  const { app_name } = useAppConfig();
  return (
    <div className="brand" style={compact ? { padding: 0 } : undefined}>
      <div className="brand-mark" />
      {app_name}
    </div>
  );
}

/* ---------------- long-press (touch: hold a tile to start selecting) ---------------- */

export function useMediaQuery(query: string): boolean {
  const get = () => typeof window !== "undefined" && !!window.matchMedia?.(query).matches;
  const [match, setMatch] = useState(get);
  useEffect(() => {
    const mq = window.matchMedia?.(query);
    if (!mq) return;
    const on = () => setMatch(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return match;
}

/**
 * Multi-select for a grid of tiles.
 *
 * - Tap/click a tile while selecting to toggle it; Shift-click selects a range.
 * - Touch: long-press a tile to start selecting; keep the finger down and drag to select a range.
 * - Mouse: press on a tile and drag across others to select a range (`mouseDrag: "always"` also
 *   starts select mode; `"selecting"` only works once select mode is on, e.g. where plain dragging
 *   reorders tiles). Dragging from an already-selected tile deselects instead.
 * - Near the top/bottom edge while dragging, the page scrolls.
 * - Deselecting the last tile leaves select mode.
 *
 * Spread `tileProps(index)` on each tile and call `click(index, event)` first in its onClick:
 * it returns true when the click was handled as a selection.
 */
export function useTileSelection<K>(keys: K[], opts: { enabled?: boolean; mouseDrag?: "always" | "selecting" } = {}) {
  const enabled = opts.enabled ?? true;
  const mouseDrag = opts.mouseDrag ?? "always";
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<K>>(new Set());
  const st = useRef({ keys, selected, selecting, enabled, mouseDrag });
  st.current = { keys, selected, selecting, enabled, mouseDrag };
  const anchor = useRef<number | null>(null);
  const consumed = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  const press = useRef<{ idx: number; x: number; y: number; type: string; id: number } | null>(null);
  const drag = useRef<{ from: number; to: number; add: boolean; base: Set<K>; result: Set<K>; x: number; y: number; scroller: HTMLElement } | null>(null);
  const raf = useRef<number | undefined>(undefined);

  const stop = useCallback(() => {
    setSelecting(false);
    setSelected(new Set());
    anchor.current = null;
  }, []);
  const start = useCallback(() => setSelecting(true), []);

  const paint = () => {
    const d = drag.current;
    if (!d) return;
    const n = new Set(d.base);
    const ks = st.current.keys;
    for (let i = Math.min(d.from, d.to); i <= Math.max(d.from, d.to); i++) {
      if (i >= ks.length) break;
      if (d.add) n.add(ks[i]);
      else n.delete(ks[i]);
    }
    d.result = n;
    setSelected(n);
  };
  const tileAt = (x: number, y: number): number | null => {
    const el = document.elementFromPoint(x, y)?.closest<HTMLElement>("[data-sel-idx]");
    return el ? Number(el.dataset.selIdx) : null;
  };
  const scrollParent = (el: Element | null): HTMLElement => {
    for (let n = el?.parentElement; n; n = n.parentElement) {
      const oy = getComputedStyle(n).overflowY;
      if ((oy === "auto" || oy === "scroll") && n.scrollHeight > n.clientHeight) return n;
    }
    return (document.scrollingElement as HTMLElement) ?? document.documentElement;
  };
  const autoScroll = () => {
    const d = drag.current;
    if (!d) return;
    const edge = 70;
    const r = d.scroller === document.scrollingElement ? { top: 0, bottom: window.innerHeight } : d.scroller.getBoundingClientRect();
    const v = d.y < r.top + edge ? -(r.top + edge - d.y) : d.y > r.bottom - edge ? d.y - (r.bottom - edge) : 0;
    if (v) {
      d.scroller.scrollTop += Math.max(-24, Math.min(24, v / 3));
      const i = tileAt(d.x, d.y);
      if (i !== null && i !== d.to) {
        d.to = i;
        paint();
      }
    }
    raf.current = requestAnimationFrame(autoScroll);
  };
  const beginDrag = (idx: number, x: number, y: number, target: Element) => {
    const { keys: ks, selected: sel, selecting: on } = st.current;
    const add = !(on && sel.has(ks[idx]));
    drag.current = { from: idx, to: idx, add, base: on ? new Set(sel) : new Set(), result: new Set(), x, y, scroller: scrollParent(target) };
    setSelecting(true);
    anchor.current = idx;
    consumed.current = true;
    paint();
    cancelAnimationFrame(raf.current!);
    raf.current = requestAnimationFrame(autoScroll);
  };

  // Window-level listeners: the finger/mouse leaves the starting tile while dragging.
  useEffect(() => {
    const move = (e: PointerEvent) => {
      const p = press.current;
      const d = drag.current;
      if (d) {
        d.x = e.clientX;
        d.y = e.clientY;
        const i = tileAt(e.clientX, e.clientY);
        if (i !== null && i !== d.to) {
          d.to = i;
          paint();
        }
        return;
      }
      if (!p || p.id !== e.pointerId) return;
      const moved = Math.hypot(e.clientX - p.x, e.clientY - p.y);
      if (p.type === "mouse") {
        if (moved > 6) {
          const target = document.elementFromPoint(p.x, p.y);
          press.current = null;
          if (target) beginDrag(p.idx, e.clientX, e.clientY, target);
        }
      } else if (moved > 10) {
        // Finger moved before the long-press fired: it's a scroll.
        window.clearTimeout(timer.current);
        press.current = null;
      }
    };
    const end = () => {
      window.clearTimeout(timer.current);
      press.current = null;
      const d = drag.current;
      if (!d) return;
      drag.current = null;
      cancelAnimationFrame(raf.current!);
      if (!d.add && d.result.size === 0) stop();
    };
    // While drag-selecting with a finger, stop the page from scrolling instead.
    const touchMove = (e: TouchEvent) => {
      if (drag.current && e.cancelable) e.preventDefault();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    window.addEventListener("touchmove", touchMove, { passive: false });
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      window.removeEventListener("touchmove", touchMove);
      cancelAnimationFrame(raf.current!);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const lastType = useRef("mouse");

  const tileProps = (idx: number) => {
    const mouseOk = enabled && (mouseDrag === "always" || selecting);
    return {
      "data-sel-idx": idx,
      onPointerDown: (e: React.PointerEvent) => {
        consumed.current = false;
        lastType.current = e.pointerType;
        if (!st.current.enabled) return;
        if (e.pointerType === "mouse") {
          if (e.button !== 0 || e.shiftKey || !mouseOk) return;
          press.current = { idx, x: e.clientX, y: e.clientY, type: "mouse", id: e.pointerId };
          return;
        }
        press.current = { idx, x: e.clientX, y: e.clientY, type: e.pointerType, id: e.pointerId };
        window.clearTimeout(timer.current);
        const target = e.currentTarget as Element;
        timer.current = window.setTimeout(() => {
          if (!press.current || press.current.idx !== idx) return;
          press.current = null;
          navigator.vibrate?.(15);
          beginDrag(idx, e.clientX, e.clientY, target);
        }, 450);
      },
      onContextMenu: (e: React.MouseEvent) => {
        if (lastType.current !== "mouse") e.preventDefault(); // no long-press menu on touch
      },
      onDragStart: mouseOk ? (e: React.DragEvent) => e.preventDefault() : undefined,
    };
  };

  /** Call first in a tile's onClick. True = handled as selection (don't open the item). */
  const click = (idx: number, e: React.MouseEvent): boolean => {
    if (consumed.current) {
      consumed.current = false;
      return true;
    }
    if (!st.current.selecting) return false;
    const ks = st.current.keys;
    const n = new Set(st.current.selected);
    if (e.shiftKey && anchor.current !== null) {
      for (let i = Math.min(anchor.current, idx); i <= Math.max(anchor.current, idx); i++) n.add(ks[i]);
    } else if (n.has(ks[idx])) n.delete(ks[idx]);
    else n.add(ks[idx]);
    anchor.current = idx;
    if (n.size === 0) stop();
    else setSelected(n);
    return true;
  };

  return { selecting, selected, setSelected, start, stop, tileProps, click };
}

/* ---------------- toasts ---------------- */

type Toast = { id: number; text: string; error?: boolean };
const ToastCtx = createContext<(text: string, error?: boolean) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, error = false) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, error }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), error ? 7000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={cls("toast", t.error && "error")}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

/* ---------------- modal ---------------- */

export function Modal({
  title,
  onClose,
  children,
  footer,
  wide,
  z,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
  /** Stacking level; raise above the full-screen viewer (3000) when opened from it. */
  z?: number;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" style={z ? { zIndex: z } : undefined} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={cls("modal", wide && "wide")} role="dialog" aria-modal="true">
        <div className="modal-head">
          <h2 style={{ margin: 0 }} className="grow">
            {title}
          </h2>
          <button className="btn ghost sm" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Confirm({
  title,
  children,
  confirmLabel,
  danger,
  busy,
  onConfirm,
  onCancel,
}: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal
      title={title}
      onClose={onCancel}
      footer={
        <>
          <button className="btn" onClick={onCancel}>
            Cancel
          </button>
          <button className={cls("btn", danger ? "danger" : "primary")} onClick={onConfirm} disabled={busy} autoFocus>
            {busy ? <span className="spinner" /> : null}
            {confirmLabel}
          </button>
        </>
      }
    >
      {children}
    </Modal>
  );
}

/* ---------------- thumbs ---------------- */

export function Thumb({
  file,
  size = 64,
  big,
  dim,
  className,
  live,
}: {
  file: Pick<FileInfo, "id" | "thumb" | "kind" | "duration" | "ext"> | null | undefined;
  size?: number | string;
  big?: boolean;
  dim?: boolean;
  className?: string;
  live?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const style = typeof size === "number" ? { width: size, height: size } : { width: size };
  if (!file) return <div className="thumb" style={style} />;
  return (
    <div className={cls("thumb", dim && "dim", className)} style={style}>
      {file.thumb && !failed ? (
        <img src={thumbUrl(file.id, big ? "l" : "s")} loading="lazy" alt="" onError={() => setFailed(true)} />
      ) : (
        <div className="ph">{file.ext.toUpperCase()}</div>
      )}
      {(file.kind === "video" || live) && (
        <div className="corner">
          {file.kind === "video" && <span className="badge">▶ {duration(file.duration)}</span>}
          {live && <span className="badge">LIVE</span>}
        </div>
      )}
    </div>
  );
}

export function Spinner() {
  return <span className="spinner" aria-label="Loading" />;
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <div className="empty">
      <Spinner /> <div className="small" style={{ marginTop: 8 }}>{what}…</div>
    </div>
  );
}

/* ---------------- icons (inline SVG, stroke = currentColor) ---------------- */

const I = (d: ReactNode) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    {d}
  </svg>
);

export const Icons = {
  overview: I(<><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>),
  review: I(<><rect x="2" y="5" width="9" height="14" rx="1.5" /><rect x="13" y="5" width="9" height="14" rx="1.5" /></>),
  albums: I(<><rect x="3" y="6" width="14" height="14" rx="2" /><path d="M7 3h12a2 2 0 0 1 2 2v12" /><path d="m3 16 4-4 4 4 2-2 4 4" /></>),
  people: I(<><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20a6.5 6.5 0 0 1 13 0" /><circle cx="17" cy="9" r="2.5" /><path d="M16 14.2A5 5 0 0 1 21.5 19" /></>),
  upload: I(<><path d="M12 16V4" /><path d="m6 10 6-6 6 6" /><path d="M4 20h16" /></>),
  browse: I(<><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="9" cy="10" r="2" /><path d="m21 16-5-5-9 9" /></>),
  map: I(<><path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2Z" /><path d="M9 4v14M15 6v14" /></>),
  pending: I(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>),
  quarantine: I(<><path d="M4 7h16l-1.5 13h-13L4 7Z" /><path d="M9 7V4h6v3M10 11v6M14 11v6" /></>),
  audit: I(<><path d="M7 3h10l4 4v14H3V3h4" /><path d="M8 12h8M8 16h8M8 8h4" /></>),
  settings: I(<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" /></>),
};
