import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
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
  upload: I(<><path d="M12 16V4" /><path d="m6 10 6-6 6 6" /><path d="M4 20h16" /></>),
  browse: I(<><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="9" cy="10" r="2" /><path d="m21 16-5-5-9 9" /></>),
  map: I(<><path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2Z" /><path d="M9 4v14M15 6v14" /></>),
  pending: I(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>),
  quarantine: I(<><path d="M4 7h16l-1.5 13h-13L4 7Z" /><path d="M9 7V4h6v3M10 11v6M14 11v6" /></>),
  audit: I(<><path d="M7 3h10l4 4v14H3V3h4" /><path d="M8 12h8M8 16h8M8 8h4" /></>),
  settings: I(<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" /></>),
};
