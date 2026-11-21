import { useEffect, useState } from "react";
import { api, type FileInfo } from "../api";
import { thumbUrl } from "../util";
import { Spinner } from "../ui";

/** Plays a video, asking the server for a browser-friendly preview (HEVC is transcoded on demand). */
export function VideoPlayer({ file, autoPlay, loop }: { file: FileInfo; autoPlay?: boolean; loop?: boolean }) {
  const [status, setStatus] = useState<"checking" | "ready" | "processing" | "failed">("checking");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const check = async () => {
      try {
        const r = await api<{ status: "ready" | "processing" | "failed"; error?: string }>(
          `/api/files/${file.id}/preview/status`,
        );
        if (!alive) return;
        setStatus(r.status);
        if (r.status === "failed") setError(r.error ?? "Could not convert this video");
        if (r.status === "processing") timer = window.setTimeout(check, 2500);
      } catch (e) {
        if (alive) {
          setStatus("failed");
          setError(String(e));
        }
      }
    };
    check();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [file.id]);

  if (status === "ready") {
    return (
      <video
        src={`/api/files/${file.id}/preview`}
        poster={file.thumb ? thumbUrl(file.id, "l") : undefined}
        controls
        autoPlay={autoPlay}
        loop={loop}
        playsInline
        onClick={(e) => e.stopPropagation()}
      />
    );
  }
  return (
    <div style={{ position: "relative", width: "100%", height: "100%", display: "grid", placeItems: "center" }}>
      {file.thumb && <img src={thumbUrl(file.id, "l")} alt="" style={{ opacity: 0.5 }} />}
      <div className="badge" style={{ position: "absolute", gap: 8, padding: "6px 12px" }}>
        {status === "failed" ? (
          <>⚠ {error}</>
        ) : (
          <>
            <Spinner /> Preparing a playable preview…
          </>
        )}
      </div>
    </div>
  );
}

/** Big still image (2048px server-side thumbnail) with optional synced zoom loupe. */
export function StillView({
  file,
  zoom,
  focus,
  onMove,
}: {
  file: FileInfo;
  zoom?: number;
  focus?: { x: number; y: number } | null;
  onMove?: (p: { x: number; y: number } | null) => void;
}) {
  if (!file.thumb) return <div className="faint">No preview ({file.ext.toUpperCase()})</div>;
  const src = thumbUrl(file.id, "l");
  if (zoom && zoom > 1) {
    const p = focus ?? { x: 50, y: 50 };
    return (
      <div
        className="zoom"
        style={{ backgroundImage: `url(${src})`, backgroundSize: `${zoom * 100}%`, backgroundPosition: `${p.x}% ${p.y}%` }}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          onMove?.({ x: ((e.clientX - r.left) / r.width) * 100, y: ((e.clientY - r.top) / r.height) * 100 });
        }}
      />
    );
  }
  return <img src={src} alt={file.name} />;
}
