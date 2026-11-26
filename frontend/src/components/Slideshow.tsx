// Album slideshow: full screen, 3-second crossfades, optional shuffle, videos played in full, Live Photo
// motion played once. Used on signed-in album pages and on public share-link pages (pass that link's URLs).
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ItemInfo } from "../api";
import type { MediaUrls } from "../media-urls";
import { Modal } from "../ui";
import { canPlayHevc } from "./MediaView";

const FADE_MS = 3000;
const QUICK_FADE_MS = 600; // when someone skips with the arrows / buttons
const PREPARE_TIMEOUT_MS = 10000;
const STALL_MS = 30000; // a video that stops making progress this long is skipped
const CONTROLS_IDLE_MS = 2500;
const SETTINGS_KEY = "slideshow-settings";

export interface SlideshowSettings {
  seconds: number; // time per photo, not counting the fade
  shuffle: boolean;
  sound: boolean; // video and Live Photo sound
}

const DEFAULTS: SlideshowSettings = { seconds: 10, shuffle: true, sound: false };
const MIN_SECONDS = 5;
const MAX_SECONDS = 60;
const clampSeconds = (n: number) => (Number.isFinite(n) ? Math.min(MAX_SECONDS, Math.max(MIN_SECONDS, Math.round(n))) : DEFAULTS.seconds);

function loadSettings(): SlideshowSettings {
  try {
    const s = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "null");
    if (s && typeof s === "object") {
      return {
        seconds: clampSeconds(Number(s.seconds)),
        shuffle: typeof s.shuffle === "boolean" ? s.shuffle : DEFAULTS.shuffle,
        sound: typeof s.sound === "boolean" ? s.sound : DEFAULTS.sound,
      };
    }
  } catch {
    /* storage unavailable: defaults */
  }
  return DEFAULTS;
}

function saveSettings(s: SlideshowSettings) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
  } catch {
    /* not essential */
  }
}

// --- what gets shown ------------------------------------------------------------------------------

interface Slide {
  kind: "image" | "video";
  file: number;
  codec: string | null;
  poster: boolean; // has a large thumbnail
  live: { file: number; codec: string | null } | null; // Live Photo motion clip
}

function toSlides(items: ItemInfo[]): Slide[] {
  const out: Slide[] = [];
  for (const it of items) {
    const p = it.primary;
    if (p.kind === "video") {
      out.push({ kind: "video", file: p.id, codec: p.codec, poster: p.thumb, live: null });
    } else if (p.thumb) {
      const c = it.is_live_photo && it.companion ? it.companion : null;
      out.push({ kind: "image", file: p.id, codec: null, poster: true, live: c ? { file: c.id, codec: c.codec } : null });
    } // images the server can't render (no thumbnail) are left out
  }
  return out;
}

interface Prepared {
  video: string | null; // playable video URL (video slides), null = not playable yet
  motion: string | null; // Live Photo motion URL
}

const withParam = (u: string, p: string) => `${u}${u.includes("?") ? "&" : "?"}${p}`;

/** URL the browser can play now, or null (still converting / failed). Asking also starts a conversion. */
async function playableUrl(urls: MediaUrls, file: number, codec: string | null): Promise<string | null> {
  try {
    const hevcHere = codec === "hevc" && canPlayHevc();
    const base = urls.previewStatus(file);
    let r = await api<{ status: string; hevc_original?: boolean }>(hevcHere ? withParam(base, "start=0") : base);
    if (r.status === "missing") {
      if (r.hevc_original && canPlayHevc()) return withParam(urls.preview(file), "original=1");
      r = await api(base); // start converting, for the next time round
    }
    return r.status === "ready" ? urls.preview(file) : null;
  } catch {
    return null;
  }
}

function loadImage(src: string): Promise<void> {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      if (img.decode) img.decode().then(resolve, resolve);
      else resolve();
    };
    img.onerror = () => resolve();
    img.src = src;
  });
}

function shuffled(n: number, avoidFirst?: number): number[] {
  const a = Array.from({ length: n }, (_, i) => i);
  for (let i = n - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  if (n > 1 && a[0] === avoidFirst) [a[0], a[1]] = [a[1], a[0]]; // don't repeat across a loop
  return a;
}

// --- button + settings prompt ---------------------------------------------------------------------

export function SlideshowButton({ items, urls, title, className }: { items: ItemInfo[]; urls: MediaUrls; title: string; className?: string }) {
  const [asking, setAsking] = useState(false);
  const [settings, setSettings] = useState<SlideshowSettings>(loadSettings);
  // The list is frozen when the slideshow starts, so page refreshes behind it don't restart it.
  const [running, setRunning] = useState<{ settings: SlideshowSettings; slides: Slide[] } | null>(null);
  const slides = toSlides(items);
  if (!slides.length) return null;
  const videos = slides.filter((s) => s.kind === "video").length;

  const start = () => {
    const s = { ...settings, seconds: clampSeconds(settings.seconds) };
    saveSettings(s);
    // Must be called straight from the click (browsers only allow full screen from a user action).
    document.documentElement.requestFullscreen?.().catch(() => {});
    setAsking(false);
    setRunning({ settings: s, slides });
  };

  return (
    <>
      <button className={className ?? "btn"} onClick={() => setAsking(true)}>▶ Slideshow</button>
      {asking && (
        <Modal
          title="Slideshow"
          onClose={() => setAsking(false)}
          footer={
            <>
              <button className="btn" onClick={() => setAsking(false)}>Cancel</button>
              <button className="btn primary" onClick={start} autoFocus>Start</button>
            </>
          }
        >
          <div className="stack ss-setup">
            <label className="ss-field">
              <span className="grow">Time per photo</span>
              <input
                type="range"
                min={MIN_SECONDS}
                max={MAX_SECONDS}
                step={1}
                value={settings.seconds}
                onChange={(e) => setSettings({ ...settings, seconds: clampSeconds(Number(e.target.value)) })}
                aria-label="Seconds per photo"
              />
              <strong className="ss-secs">{settings.seconds} s</strong>
            </label>
            <div className="small faint">Plus a 3-second fade between photos.</div>
            <label className="check">
              <input type="checkbox" checked={settings.shuffle} onChange={(e) => setSettings({ ...settings, shuffle: e.target.checked })} />
              Shuffle
            </label>
            <label className="check">
              <input type="checkbox" checked={settings.sound} onChange={(e) => setSettings({ ...settings, sound: e.target.checked })} />
              Video sound
            </label>
            <div className="small faint">
              {slides.length} {slides.length === 1 ? "item" : "items"}
              {videos > 0 && ` · videos play in full`} · repeats until you exit (Esc)
            </div>
          </div>
        </Modal>
      )}
      {running && <SlideshowPlayer slides={running.slides} urls={urls} settings={running.settings} title={title} onClose={() => setRunning(null)} />}
    </>
  );
}

// --- the player -----------------------------------------------------------------------------------

interface Shown {
  n: number; // unique per showing (keys, timers)
  index: number; // into slides
  prep: Prepared;
}

function SlideshowPlayer({
  slides,
  urls,
  settings,
  title,
  onClose,
}: {
  slides: Slide[];
  urls: MediaUrls;
  settings: SlideshowSettings;
  title: string;
  onClose: () => void;
}) {
  const [view, setView] = useState<{ slots: [Shown | null, Shown | null]; front: 0 | 1; fade: number }>({
    slots: [null, null],
    front: 0,
    fade: FADE_MS,
  });
  const [paused, setPaused] = useState(false);
  const [controls, setControls] = useState(true);
  const seq = useRef<number[]>(settings.shuffle ? shuffled(slides.length) : slides.map((_, i) => i));
  const pos = useRef(-1);
  const counter = useRef(0);
  const goToken = useRef(0);
  const cache = useRef(new Map<number, Promise<Prepared>>());
  const viewRef = useRef(view);
  viewRef.current = view;
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  const prepare = useCallback(
    (index: number): Promise<Prepared> => {
      const hit = cache.current.get(index);
      if (hit) return hit;
      const s = slides[index];
      const p = (async (): Promise<Prepared> => {
        if (s.kind === "video") {
          const [video] = await Promise.all([playableUrl(urls, s.file, s.codec), s.poster ? loadImage(urls.thumb(s.file, "l")) : null]);
          return { video, motion: null };
        }
        const [, motion] = await Promise.all([loadImage(urls.thumb(s.file, "l")), s.live ? playableUrl(urls, s.live.file, s.live.codec) : null]);
        return { video: null, motion };
      })();
      cache.current.set(index, p);
      // A video that isn't playable yet is asked about again next time round.
      p.then((r) => {
        if (s.kind === "video" ? !r.video : s.live ? !r.motion : false) cache.current.delete(index);
      });
      return p;
    },
    [slides, urls],
  );

  const nextPos = (dir: 1 | -1): { p: number; reshuffle: boolean } => {
    const p = pos.current + dir;
    if (p >= seq.current.length) return { p: 0, reshuffle: settings.shuffle };
    if (p < 0) return { p: seq.current.length - 1, reshuffle: false };
    return { p, reshuffle: false };
  };

  const go = useCallback(
    async (dir: 1 | -1, quick: boolean) => {
      const token = ++goToken.current;
      const { p, reshuffle } = nextPos(dir);
      if (reshuffle) {
        const last = seq.current[pos.current];
        seq.current = shuffled(slides.length, last);
        cache.current.clear();
      }
      const index = seq.current[p];
      const timeout = new Promise<Prepared>((r) => setTimeout(() => r({ video: null, motion: null }), PREPARE_TIMEOUT_MS));
      const prep = await Promise.race([prepare(index), timeout]);
      if (token !== goToken.current) return; // superseded by a newer skip
      pos.current = p;
      const shown: Shown = { n: ++counter.current, index, prep };
      setView((v) => {
        const back = (1 - v.front) as 0 | 1;
        const slots: [Shown | null, Shown | null] = [...v.slots];
        slots[back] = shown;
        return { slots, front: back, fade: quick ? QUICK_FADE_MS : FADE_MS };
      });
      // Get the following one ready while this one shows.
      const ahead = nextPos(1);
      if (!ahead.reshuffle) prepare(seq.current[ahead.p]);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [prepare, slides.length, settings.shuffle],
  );

  // First slide
  useEffect(() => {
    go(1, false);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // After a fade, drop the slide that faded out (stops its video, frees memory).
  const current = view.slots[view.front];
  useEffect(() => {
    if (!current) return;
    const t = window.setTimeout(() => {
      setView((v) => {
        const back = (1 - v.front) as 0 | 1;
        if (!v.slots[back]) return v;
        const slots: [Shown | null, Shown | null] = [...v.slots];
        slots[back] = null;
        return { ...v, slots };
      });
    }, view.fade + 50);
    return () => window.clearTimeout(t);
  }, [current?.n, view.fade]); // eslint-disable-line react-hooks/exhaustive-deps

  // Photo timer (and videos that can't play yet): fade + time per photo, paused when paused.
  const remaining = useRef(0);
  const timedSlide = current && (slides[current.index].kind === "image" || !current.prep.video);
  useEffect(() => {
    if (current) remaining.current = view.fade + settings.seconds * 1000;
  }, [current?.n]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!current || !timedSlide || paused) return;
    const started = Date.now();
    const t = window.setTimeout(() => go(1, false), remaining.current);
    return () => {
      window.clearTimeout(t);
      remaining.current = Math.max(0, remaining.current - (Date.now() - started));
    };
  }, [current?.n, timedSlide, paused, go]); // eslint-disable-line react-hooks/exhaustive-deps

  // Full screen: leaving it (Esc, browser button) ends the slideshow.
  useEffect(() => {
    let was = !!document.fullscreenElement;
    const onChange = () => {
      const now = !!document.fullscreenElement;
      if (was && !now) closeRef.current();
      was = now;
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => {
      document.removeEventListener("fullscreenchange", onChange);
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    };
  }, []);

  // Keep the screen awake while it runs.
  useEffect(() => {
    type Lock = { release: () => Promise<void> };
    const nav = navigator as Navigator & { wakeLock?: { request: (t: "screen") => Promise<Lock> } };
    let lock: Lock | null = null;
    const take = () => nav.wakeLock?.request("screen").then((l) => (lock = l)).catch(() => {});
    const onVis = () => document.visibilityState === "visible" && take();
    take();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      lock?.release().catch(() => {});
    };
  }, []);

  // Controls appear on mouse move / tap and hide again when idle.
  const idle = useRef<number | undefined>();
  const poke = useCallback(() => {
    setControls(true);
    window.clearTimeout(idle.current);
    idle.current = window.setTimeout(() => setControls(false), CONTROLS_IDLE_MS);
  }, []);
  useEffect(() => {
    poke();
    return () => window.clearTimeout(idle.current);
  }, [poke]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeRef.current();
      else if (e.key === "ArrowRight") go(1, true);
      else if (e.key === "ArrowLeft") go(-1, true);
      else if (e.key === " " || e.key === "k") setPaused((p) => !p);
      else return;
      e.preventDefault();
      poke();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, poke]);

  const stop = (e: React.SyntheticEvent) => e.stopPropagation();
  return (
    <div className={`ss ${controls ? "" : "ss-idle"}`} onMouseMove={poke} onClick={poke} role="dialog" aria-label={`Slideshow: ${title}`}>
      {view.slots.map((shown, i) => (
        <div key={i} className="ss-slot" style={{ opacity: i === view.front && shown ? 1 : 0, transition: `opacity ${view.fade}ms ease-in-out` }}>
          {shown && (
            <SlideView
              key={shown.n}
              slide={slides[shown.index]}
              prep={shown.prep}
              urls={urls}
              active={i === view.front}
              paused={paused}
              sound={settings.sound}
              fadeMs={view.fade}
              onDone={() => go(1, false)}
            />
          )}
        </div>
      ))}
      {!current && <div className="ss-loading">Loading…</div>}
      <div className="ss-bar" onClick={stop} onMouseMove={stop}>
        <span className="ss-title">{title}</span>
        <button className="ss-btn" onClick={() => { go(-1, true); poke(); }} aria-label="Previous">⏮</button>
        <button className="ss-btn" onClick={() => { setPaused((p) => !p); poke(); }} aria-label={paused ? "Play" : "Pause"}>
          {paused ? "▶" : "⏸"}
        </button>
        <button className="ss-btn" onClick={() => { go(1, true); poke(); }} aria-label="Next">⏭</button>
        <span className="ss-count">{pos.current + 1} / {slides.length}</span>
        <button className="ss-btn" onClick={() => closeRef.current()} aria-label="Exit slideshow">✕</button>
      </div>
      {paused && <div className="ss-paused">Paused</div>}
    </div>
  );
}

function SlideView({
  slide,
  prep,
  urls,
  active,
  paused,
  sound,
  fadeMs,
  onDone,
}: {
  slide: Slide;
  prep: Prepared;
  urls: MediaUrls;
  active: boolean;
  paused: boolean;
  sound: boolean;
  fadeMs: number;
  onDone: () => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const motionEl = useRef<HTMLVideoElement>(null);
  const [motion, setMotion] = useState<"waiting" | "playing" | "done">("waiting");
  const doneRef = useRef(onDone);
  doneRef.current = onDone;
  const finished = useRef(false);
  const finish = () => {
    if (finished.current || !active) return;
    finished.current = true;
    doneRef.current();
  };

  // Play with sound if allowed; if the browser refuses unmuted playback, play muted instead.
  const play = (el: HTMLVideoElement | null) => {
    if (!el) return;
    el.muted = !sound;
    el.play().catch(() => {
      el.muted = true;
      el.play().catch(() => {});
    });
  };

  // Videos: play while this slide is the active one; skip on errors or long stalls.
  useEffect(() => {
    const el = video.current;
    if (!el) return;
    if (active && !paused) play(el);
    else el.pause();
  }, [active, paused]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!prep.video || !active || paused) return;
    let last = -1;
    let since = Date.now();
    const t = window.setInterval(() => {
      const el = video.current;
      if (!el) return;
      if (el.currentTime !== last) {
        last = el.currentTime;
        since = Date.now();
      } else if (Date.now() - since > STALL_MS) finish();
    }, 2000);
    return () => window.clearInterval(t);
  }, [prep.video, active, paused]); // eslint-disable-line react-hooks/exhaustive-deps

  // Live Photo: once the photo has faded in, play the motion once, then settle on the still.
  useEffect(() => {
    if (!prep.motion || !active || motion !== "waiting") return;
    const t = window.setTimeout(() => setMotion("playing"), fadeMs);
    return () => window.clearTimeout(t);
  }, [prep.motion, active, motion, fadeMs]);
  useEffect(() => {
    const el = motionEl.current;
    if (!el || motion !== "playing") return;
    if (paused || !active) el.pause();
    else play(el);
  }, [motion, paused, active]); // eslint-disable-line react-hooks/exhaustive-deps

  const poster = slide.poster ? urls.thumb(slide.file, "l") : undefined;
  if (slide.kind === "video") {
    if (prep.video) {
      return (
        <video
          ref={video}
          className="ss-media"
          src={prep.video}
          poster={poster}
          playsInline
          muted={!sound}
          onEnded={finish}
          onError={() => window.setTimeout(finish, 1500)}
        />
      );
    }
    return (
      <>
        {poster && <img className="ss-media" src={poster} alt="" draggable={false} />}
        <div className="ss-note">This video is still being prepared for playback; it will play next time round.</div>
      </>
    );
  }
  return (
    <>
      <img className="ss-media" src={urls.thumb(slide.file, "l")} alt="" draggable={false} />
      {prep.motion && motion !== "waiting" && (
        <video
          ref={motionEl}
          className={`ss-media ss-motion ${motion === "done" ? "ss-motion-done" : ""}`}
          src={prep.motion}
          playsInline
          muted={!sound}
          onEnded={() => setMotion("done")}
          onError={() => setMotion("done")}
        />
      )}
    </>
  );
}
