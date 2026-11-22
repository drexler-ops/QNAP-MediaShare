import { useEffect, useRef, useState } from "react";
import { api, type FileInfo } from "../api";
import { cls, thumbUrl } from "../util";
import { useMediaUrls } from "../media-urls";
import { Spinner } from "../ui";

/** Plays a video, asking the server for a browser-friendly preview (HEVC is transcoded on demand). */
/**
 * `customControls` (touch screens in the viewer): our own play/seek/mute bar instead of the browser's,
 * because native controls swallow touches and would block swiping to the next item.
 */
export function VideoPlayer({ file, autoPlay, loop, customControls }: { file: FileInfo; autoPlay?: boolean; loop?: boolean; customControls?: boolean }) {
  const urls = useMediaUrls();
  const [st, setSt] = useState<PreviewStatus>({ status: "checking" });
  const [attempt, setAttempt] = useState(0); // bump to retry a failed conversion
  const [playError, setPlayError] = useState(false);
  const [buffering, setBuffering] = useState(false);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    let retry = attempt > 0;
    setSt({ status: "checking" });
    setPlayError(false);
    const check = async () => {
      try {
        const base = urls.previewStatus(file.id);
        const r = await api<PreviewStatus>(retry ? `${base}${base.includes("?") ? "&" : "?"}retry=1` : base);
        retry = false;
        if (!alive) return;
        setSt(r);
        if (r.status === "processing") timer = window.setTimeout(check, 1500);
      } catch (e) {
        if (alive) setSt({ status: "failed", error: `Couldn't check this video (${e instanceof Error ? e.message : String(e)}).` });
      }
    };
    check();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [file.id, urls, attempt]);

  const poster = file.thumb ? urls.thumb(file.id, "l") : undefined;
  if (st.status === "ready" && !playError) {
    const events = {
      onError: () => setPlayError(true),
      onWaiting: () => setBuffering(true),
      onPlaying: () => setBuffering(false),
      onCanPlay: () => setBuffering(false),
    };
    const note = st.preview_seconds ? (
      <div className="vnote">Playing the first {Math.round(st.preview_seconds / 60)} min · download for the whole video</div>
    ) : null;
    return (
      <>
        {customControls ? (
          <TouchVideo src={urls.preview(file.id)} poster={poster} autoPlay={autoPlay} loop={loop} events={events} />
        ) : (
          <video src={urls.preview(file.id)} poster={poster} controls autoPlay={autoPlay} loop={loop} playsInline onClick={(e) => e.stopPropagation()} {...events} />
        )}
        {buffering && <div className="vwait-msg vwait-float"><Spinner /> Loading…</div>}
        {note}
      </>
    );
  }

  // Not playable yet: the poster fitted to the screen (dimmed) with a clear status in the middle.
  const msg =
    playError ? (
      <>⚠ This video can't play in this browser. Download it to watch it.</>
    ) : st.status === "failed" ? (
      <>
        <span>⚠ {st.error ?? "This video couldn't be converted for playback."}</span>
        <button className="btn sm" onClick={(e) => { e.stopPropagation(); setAttempt((n) => n + 1); }} onPointerDown={(e) => e.stopPropagation()}>Try again</button>
      </>
    ) : st.status === "processing" && st.stage === "running" ? (
      <><Spinner /> Converting this video for playback…</>
    ) : st.status === "processing" ? (
      <><Spinner /> Waiting to convert{st.ahead ? ` (${st.ahead} other video${st.ahead === 1 ? "" : "s"} first)` : ""}…</>
    ) : (
      <><Spinner /> Loading video…</>
    );
  return (
    <div className="vwait">
      {poster && <img src={poster} alt="" draggable={false} />}
      <div className={cls("vwait-msg", (playError || st.status === "failed") && "error")} role="status">{msg}</div>
    </div>
  );
}

type PreviewStatus = {
  status: "checking" | "ready" | "processing" | "failed";
  stage?: "queued" | "running";
  ahead?: number;
  error?: string;
  preview_seconds?: number;
};

type VideoEvents = {
  onError: () => void;
  onWaiting: () => void;
  onPlaying: () => void;
  onCanPlay: () => void;
};

function clock(sec: number): string {
  if (!isFinite(sec) || sec < 0) sec = 0;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** Video with a minimal overlay control bar; touches on the picture itself fall through to the viewer. */
function TouchVideo({ src, poster, autoPlay, loop, events }: { src: string; poster?: string; autoPlay?: boolean; loop?: boolean; events: VideoEvents }) {
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
        {...events}
        onPlaying={() => { setPlaying(true); events.onPlaying(); }}
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
