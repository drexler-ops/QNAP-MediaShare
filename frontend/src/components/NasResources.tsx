import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Loading } from "../ui";
import { bytes } from "../util";

interface Usage { busy: number; user: number; system: number; iowait: number }
interface Worker { name: string; processes: number; used: number | null; limit: number | null; online: boolean; seen_seconds_ago: number }
interface SystemSnap {
  cpu: Usage & { cores: number[]; load: [number, number, number] };
  memory: { total: number; available: number; used: number; cache: number; swap_total: number; swap_used: number };
  uptime: number | null;
  storage: { labels: string[]; total: number; used: number; free: number }[];
  app_data_bytes: number | null;
  api: { used: number | null; limit: number | null };
  workers: Worker[];
  jobs: Record<string, Record<string, number>>;
  converting_on_demand: number;
}

const POLL_MS = 5_000;
const HISTORY = 60; // 5 minutes at one reading per 5 s

/** Load level for a percentage: reserved status colours, always shown with a word, never colour alone. */
function level(pct: number): { cls: string; word: string } | null {
  if (pct >= 90) return { cls: "crit", word: "High" };
  if (pct >= 75) return { cls: "warn", word: "Busy" };
  return null;
}

function Meter({ pct, label, sub }: { pct: number; label: string; sub?: string }) {
  const lv = level(pct);
  return (
    <div className="meter-wrap">
      <div className="meter-head">
        <span>{label}</span>
        <span className="mono">
          {lv && <span className={`lvl ${lv.cls}`}>● {lv.word}</span>} {pct.toFixed(0)}%
        </span>
      </div>
      <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)} aria-label={label}>
        <div className={lv ? lv.cls : ""} style={{ width: `${Math.min(Math.max(pct, 0), 100)}%` }} />
      </div>
      {sub && <div className="small muted">{sub}</div>}
    </div>
  );
}

/** CPU % over the last few minutes, with a hover readout. Single series: the card title names it. */
function Sparkline({ points }: { points: number[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = HISTORY - 1;
  const x = (i: number) => W - (points.length - 1 - i);
  const line = points.map((p, i) => `${x(i)},${100 - p}`).join(" ");
  const area = points.length > 1 ? `${x(0)},100 ${line} ${x(points.length - 1)},100` : "";
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const xi = Math.round(((e.clientX - r.left) / r.width) * W);
    const i = points.length - 1 - (W - xi);
    setHover(i >= 0 && i < points.length ? i : null);
  };
  const ago = hover === null ? 0 : (points.length - 1 - hover) * (POLL_MS / 1000);
  return (
    <div className="spark">
      <svg viewBox={`0 0 ${W} 100`} preserveAspectRatio="none" onPointerMove={onMove} onPointerLeave={() => setHover(null)}
        role="img" aria-label={`CPU over the last ${Math.round((points.length * POLL_MS) / 60000)} minutes`}>
        <line x1="0" x2={W} y1="50" y2="50" className="grid" />
        {area && <polygon points={area} className="area" />}
        {points.length > 1 && <polyline points={line} className="ln" />}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1="0" y2="100" className="cross" />}
      </svg>
      <div className="spark-foot small muted">
        {hover !== null ? (
          <span className="mono">{ago === 0 ? "now" : `${ago >= 60 ? `${Math.floor(ago / 60)} min ` : ""}${ago % 60 ? `${ago % 60} s` : ""} ago`} · {points[hover].toFixed(0)}%</span>
        ) : (
          <span>Last {Math.max(1, Math.round((points.length * POLL_MS) / 60000))} min · hover for values</span>
        )}
      </div>
    </div>
  );
}

function duration(sec: number | null): string {
  if (sec === null) return "";
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}

const JOB_NAMES: Record<string, string> = {
  scan: "Scanning", process: "Indexing", faces: "Faces", faces_scan: "Face scan", group: "Grouping",
  preview: "Video copies", purge: "Purge",
};

export default function NasResources() {
  const q = useQuery({
    queryKey: ["system"],
    queryFn: () => api<SystemSnap>("/api/admin/system"),
    refetchInterval: POLL_MS, // paused automatically while the tab is hidden
  });
  const hist = useRef<number[]>([]);
  const [history, setHistory] = useState<number[]>([]);
  useEffect(() => {
    if (!q.data) return;
    hist.current = [...hist.current, q.data.cpu.busy].slice(-HISTORY);
    setHistory(hist.current);
  }, [q.dataUpdatedAt]); // eslint-disable-line react-hooks/exhaustive-deps

  if (q.isError) return <section className="card pad" style={{ gridColumn: "1 / -1" }}><h2>NAS resources</h2><p className="muted">Couldn't read system stats ({String((q.error as Error).message)}).</p></section>;
  if (!q.data) return <section className="card pad" style={{ gridColumn: "1 / -1" }}><Loading /></section>;
  const s = q.data;
  const cores = s.cpu.cores.length || 1;
  const memPct = (s.memory.used / Math.max(s.memory.total, 1)) * 100;
  const jobs = Object.entries(s.jobs).filter(([, v]) => (v.queued ?? 0) + (v.running ?? 0) > 0);

  return (
    <section className="card pad stack" style={{ gridColumn: "1 / -1" }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>NAS resources <span className="badge accent">Admin</span></h2>
        <span className="small muted">{s.uptime !== null && `Up ${duration(s.uptime)} · `}updates every {POLL_MS / 1000} s</span>
      </div>

      <div className="res-grid">
        <div className="res-card">
          <div className="res-title">CPU</div>
          <div className="res-num">{s.cpu.busy.toFixed(0)}<span>%</span></div>
          <Sparkline points={history} />
          <div className="small muted">
            Programs {s.cpu.user.toFixed(0)}% · system {s.cpu.system.toFixed(0)}% · waiting on disk {s.cpu.iowait.toFixed(0)}%
          </div>
          <div className="small muted">
            Load {s.cpu.load.map((l) => l.toFixed(2)).join(" / ")} (1/5/15 min, {cores} cores: {cores.toFixed(0)} = fully busy)
          </div>
          <div className="cores" aria-label="Per-core usage">
            {s.cpu.cores.map((c, i) => (
              <div key={i} className="core" title={`Core ${i + 1}: ${c.toFixed(0)}%`}>
                <div className={level(c)?.cls ?? ""} style={{ height: `${Math.max(c, 2)}%` }} />
              </div>
            ))}
          </div>
        </div>

        <div className="res-card">
          <div className="res-title">Memory</div>
          <div className="res-num">{bytes(s.memory.available)}<span> free</span></div>
          <Meter pct={memPct} label={`${bytes(s.memory.used)} of ${bytes(s.memory.total)} in use`}
            sub={`Plus ${bytes(s.memory.cache)} of file cache, released automatically when needed`} />
          {s.memory.swap_total > 0 && (
            <Meter pct={(s.memory.swap_used / s.memory.swap_total) * 100} label={`Swap: ${bytes(s.memory.swap_used)} of ${bytes(s.memory.swap_total)}`} />
          )}
        </div>

        <div className="res-card">
          <div className="res-title">Storage</div>
          {s.storage.map((v) => (
            <Meter key={v.labels.join()} pct={(v.used / Math.max(v.total, 1)) * 100} label={v.labels.join(" + ")}
              sub={`${bytes(v.free)} free of ${bytes(v.total)}`} />
          ))}
          {s.app_data_bytes !== null && (
            <div className="small muted">Thumbnails &amp; playable video copies: {bytes(s.app_data_bytes)}</div>
          )}
        </div>

        <div className="res-card">
          <div className="res-title">App</div>
          <dl className="kv">
            <dt>Web server</dt>
            <dd>{s.api.used !== null ? `${bytes(s.api.used)}${s.api.limit ? ` of ${bytes(s.api.limit)}` : ""}` : "—"}</dd>
            {s.workers.map((w) => (
              <FragmentRow key={w.name} w={w} />
            ))}
            <dt>Activity</dt>
            <dd>
              {jobs.length === 0 && s.converting_on_demand === 0 ? (
                <span className="muted">Idle</span>
              ) : (
                <>
                  {jobs.map(([t, v]) => (
                    <div key={t}>{JOB_NAMES[t] ?? t}: {v.running ?? 0} running{v.queued ? ` · ${v.queued} queued` : ""}</div>
                  ))}
                  {s.converting_on_demand > 0 && <div>Converting a video someone opened</div>}
                </>
              )}
            </dd>
          </dl>
        </div>
      </div>
    </section>
  );
}

function FragmentRow({ w }: { w: Worker }) {
  const label = w.name === "nas" ? "NAS worker" : w.name === "pc" ? "Helper PC" : `Worker ${w.name}`;
  return (
    <>
      <dt>{label}</dt>
      <dd>
        {w.online ? (
          <>{w.used !== null ? bytes(w.used) : "—"}{w.limit ? ` of ${bytes(w.limit)}` : ""} · {w.processes} processes</>
        ) : (
          <span className="muted">Offline (last seen {duration(w.seen_seconds_ago)} ago)</span>
        )}
      </dd>
    </>
  );
}
