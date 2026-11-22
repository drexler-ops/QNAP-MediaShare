import { useEffect, useRef, useState } from "react";
import { api, type FileInfo } from "../api";
import { thumbUrl } from "../util";
import { useMediaUrls } from "../media-urls";
import { Spinner } from "../ui";

/** Plays a video, asking the server for a browser-friendly preview (HEVC is transcoded on demand). */
/**
 * `customControls` (touch screens in the viewer): our own play/seek/mute bar instead of the browser's,
 * because native controls swallow touches and would block swiping to the next item.
 */
export function VideoPlayer({ file, autoPlay, loop, customControls }: { file: FileInfo; autoPlay?: boolean; loop?: boolean; customControls?: boolean }) {
  const urls = useMediaUrls();
  const [status, setStatus] = useState<"checking" | "ready" | "processing" | "failed">("checking");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const check = async () => {
      try {
        const r = await api<{ status: "ready" | "processing" | "failed"; error?: string }>(
          urls.previewStatus(file.id),
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
  }, [file.id, urls]);

  if (status === "ready" && customControls) {
    return <TouchVideo src={urls.preview(file.id)} poster={file.thumb ? urls.thumb(file.id, "l") : undefined} autoPlay={autoPlay} loop={loop} />;
  }
  if (status === "ready") {
    return (
      <video
        src={urls.preview(file.id)}
        poster={file.thumb ? urls.thumb(file.id, "l") : undefined}
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
      {file.thumb && <img src={urls.thumb(file.id, "l")} alt="" style={{ opacity: 0.5 }} />}
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

function clock(sec: number): string {
  if (!isFinite(sec) || sec < 0) sec = 0;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** Video with a minimal overlay control bar; touches on the picture itself fall through to the viewer. */
function TouchVideo({ src, poster, autoPlay, loop }: { src: string; poster?: string; autoPlay?: boolean; loop?: boolean }) {
  const v = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [t, setT] = useState(0);
  const [dur, setDur] = useState(0);
  const [muted, setMuted] = useState(false);
  const stop = (e: React.PointerEvent | React.MouseEvent) => e.stopPropagation(); // not a swipe/tap on the stage
  const toggle = () => {
    const el = v.current;
    if (!el) return;
    if (el.paused) el.play().catch(() => {});
    else el.pause();
  };
  return (
    <>
      <video
        ref={v}
        src={src}
        poster={poster}
        autoPlay={autoPlay}
        loop={loop}
        muted={muted}
        playsInline
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onTimeUpdate={(e) => setT(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => setDur(e.currentTarget.duration)}
        onDurationChange={(e) => setDur(e.currentTarget.duration)}
        onVolumeChange={(e) => setMuted(e.currentTarget.muted)}
      />
      {!playing && (
        <button className="vplay" onPointerDown={stop} onClick={(e) => { e.stopPropagation(); toggle(); }} aria-label="Play">
          <svg viewBox="0 0 24 24" width="30" height="30" aria-hidden="true"><path d="M8 5v14l11-7z" fill="currentColor" /></svg>
        </button>
      )}
      <div className="vctl" onPointerDown={stop} onPointerUp={stop} onClick={stop}>
        <button className="vctl-btn" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
          {playing ? (
            <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M7 5h4v14H7zM13 5h4v14h-4z" fill="currentColor" /></svg>
          ) : (
            <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M8 5v14l11-7z" fill="currentColor" /></svg>
          )}
        </button>
        <span className="vctl-time">{clock(t)}</span>
        <input
          className="vctl-seek"
          type="range"
          min={0}
          max={dur || 0}
          step={0.05}
          value={Math.min(t, dur || 0)}
          onChange={(e) => {
            const el = v.current;
            if (el) el.currentTime = Number(e.target.value);
            setT(Number(e.target.value));
          }}
          aria-label="Seek"
        />
        <span className="vctl-time">{clock(dur)}</span>
        <button className="vctl-btn" onClick={() => setMuted((m) => !m)} aria-label={muted ? "Unmute" : "Mute"}>
          {muted ? "🔇" : "🔊"}
        </button>
      </div>
    </>
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
