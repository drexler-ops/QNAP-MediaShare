import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, post, type Me, type Settings, type UserRow } from "../api";
import { Confirm, Loading, useToast } from "../ui";
import { bytes, date } from "../util";
import NasResources from "../components/NasResources";

interface Status {
  jobs: Record<string, Record<string, number>>;
  files: { by_process_state: Record<string, number>; by_status: Record<string, number>; bytes: number };
  recent_jobs: { id: number; type: string; state: string; worker: string | null; created_at: string; finished_at: string | null; result: Record<string, unknown> | null; error: string | null }[];
  file_errors: { id: number; path: string; error: string | null }[];
  active_workers: string[];
}

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

export default function SettingsPage({ me }: { me: Me }) {
  return (
    <>
      <div className="page-head">
        <h1>Settings</h1>
      </div>
      <div className="settings-grid">
        <Account me={me} />
        {me.is_admin && <Users me={me} />}
        <Detection />
        {me.is_admin && <NasResources />}
        <SystemStatus />
      </div>
    </>
  );
}

function Account({ me }: { me: Me }) {
  const toast = useToast();
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const m = useMutation({
    mutationFn: () => post("/api/auth/password", { current_password: cur, new_password: next }),
    onSuccess: () => { toast("Password changed."); setCur(""); setNext(""); },
    onError: (e) => toast(errMsg(e), true),
  });
  return (
    <section className="card pad stack">
      <h2>My account — {me.name}</h2>
      <label className="field">Current password<input type="password" value={cur} onChange={(e) => setCur(e.target.value)} /></label>
      <label className="field">New password (6+ characters)<input type="password" value={next} onChange={(e) => setNext(e.target.value)} /></label>
      <div><button className="btn primary" disabled={!cur || next.length < 6 || m.isPending} onClick={() => m.mutate()}>Change password</button></div>
    </section>
  );
}

function Users({ me }: { me: Me }) {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["users"], queryFn: () => api<{ users: UserRow[]; unassigned: number }>("/api/users") });
  const [folders, setFolders] = useState<Record<number, string>>({});
  const [nu, setNu] = useState({ name: "", home_folder: "", is_admin: false });
  const [confirmReset, setConfirmReset] = useState<UserRow | null>(null);

  const done = (msg: string) => {
    toast(msg);
    qc.invalidateQueries({ queryKey: ["users"] });
    qc.invalidateQueries({ queryKey: ["facets"] });
  };
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Partial<UserRow> }) => api<UserRow>(`/api/users/${id}`, { method: "PATCH", json: body }),
    onSuccess: (u) => {
      setFolders((f) => { const n = { ...f }; delete n[u.id]; return n; });
      done(`Saved ${u.name}.`);
    },
    onError: (e) => toast(errMsg(e), true),
  });
  const add = useMutation({
    mutationFn: () => post<UserRow>("/api/users", nu),
    onSuccess: (u) => {
      setNu({ name: "", home_folder: "", is_admin: false });
      done(`Added ${u.name}. They'll choose a password the first time they sign in.`);
    },
    onError: (e) => toast(errMsg(e), true),
  });
  const reset = useMutation({
    mutationFn: (u: UserRow) => post<UserRow>(`/api/users/${u.id}/reset-password`),
    onSuccess: (u) => { setConfirmReset(null); done(`${u.name} will choose a new password at their next sign-in.`); },
    onError: (e) => toast(errMsg(e), true),
  });

  return (
    <section className="card pad stack" style={{ gridColumn: "1 / -1" }}>
      <h2>Users <span className="badge accent">Admin</span></h2>
      <p className="muted small" style={{ margin: 0 }}>
        Each user owns the files in their top-level folder of the library. Only the owner can approve deleting their files; files
        outside every user's folder are "unassigned" ({q.data?.unassigned.toLocaleString() ?? 0}) and anyone can approve them.
        New users and users whose password you reset choose their own password at their next sign-in.
      </p>
      {!q.data ? <Loading /> : (
        <table className="tbl">
          <thead><tr><th>Name</th><th>Folder</th><th>Files</th><th>Status</th><th>Admin</th><th /></tr></thead>
          <tbody>
            {q.data.users.map((u) => {
              const folder = folders[u.id] ?? u.home_folder ?? "";
              const dirty = folders[u.id] !== undefined && folders[u.id] !== (u.home_folder ?? "");
              return (
                <tr key={u.id} style={u.is_active ? undefined : { opacity: 0.55 }}>
                  <td style={{ fontWeight: 600 }}>{u.name}{u.id === me.id && <span className="faint small"> (you)</span>}</td>
                  <td>
                    <div className="row" style={{ flexWrap: "nowrap" }}>
                      <input type="text" value={folder} placeholder="none" style={{ width: 130 }}
                        onChange={(ev) => setFolders((f) => ({ ...f, [u.id]: ev.target.value }))} />
                      {dirty && (
                        <button className="btn sm primary" disabled={patch.isPending}
                          onClick={() => patch.mutate({ id: u.id, body: { home_folder: folders[u.id] } })}>Save</button>
                      )}
                    </div>
                  </td>
                  <td className="small muted">{u.files.toLocaleString()}</td>
                  <td>
                    {!u.is_active ? <span className="badge">Deactivated</span>
                      : u.must_set_password ? <span className="badge warn">Sets password at next sign-in</span>
                      : <span className="badge keep">Active</span>}
                  </td>
                  <td>
                    <input type="checkbox" checked={u.is_admin} aria-label={`${u.name} is admin`} disabled={patch.isPending}
                      onChange={(ev) => patch.mutate({ id: u.id, body: { is_admin: ev.target.checked } })} />
                  </td>
                  <td>
                    <div className="row" style={{ justifyContent: "flex-end", flexWrap: "nowrap" }}>
                      <button className="btn sm" disabled={!u.is_active || u.must_set_password} onClick={() => setConfirmReset(u)}>
                        Reset password
                      </button>
                      <button className="btn sm ghost" disabled={u.id === me.id || patch.isPending}
                        onClick={() => patch.mutate({ id: u.id, body: { is_active: !u.is_active } })}>
                        {u.is_active ? "Deactivate" : "Reactivate"}
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <h3 style={{ marginTop: 8 }}>Add a user</h3>
      <form className="row" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <input type="text" placeholder="Name" value={nu.name} onChange={(e) => setNu({ ...nu, name: e.target.value })} style={{ width: 150 }} />
        <input type="text" placeholder="Folder (e.g. their name)" value={nu.home_folder} onChange={(e) => setNu({ ...nu, home_folder: e.target.value })} style={{ width: 190 }} />
        <label className="check"><input type="checkbox" checked={nu.is_admin} onChange={(e) => setNu({ ...nu, is_admin: e.target.checked })} /> Admin</label>
        <button className="btn primary" disabled={!nu.name.trim() || add.isPending}>Add user</button>
      </form>
      {confirmReset && (
        <Confirm
          title={`Reset ${confirmReset.name}'s password?`}
          confirmLabel="Reset password"
          busy={reset.isPending}
          onCancel={() => setConfirmReset(null)}
          onConfirm={() => reset.mutate(confirmReset)}
        >
          <p style={{ marginTop: 0 }}>
            {confirmReset.name} is signed out everywhere. Their current password stops working, and the next time someone signs in as{" "}
            <strong>{confirmReset.name}</strong> they'll be asked to choose a new one, so let them know to sign in soon.
          </p>
        </Confirm>
      )}
    </section>
  );
}

function Num({ label, value, onChange, step = 1, hint }: { label: string; value: number; onChange: (n: number) => void; step?: number; hint?: string }) {
  return (
    <label className="field" title={hint}>
      {label}
      <input type="number" value={value} step={step} min={0} onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  );
}

function Detection() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api<Settings>("/api/admin/settings") });
  const [s, setS] = useState<Settings | null>(null);
  useEffect(() => { if (q.data) setS(q.data); }, [q.data]);

  const save = useMutation({
    mutationFn: (body: Partial<Settings>) => api<Settings>("/api/admin/settings", { method: "PUT", json: body }),
    onSuccess: (r) => { toast("Settings saved. Use “Re-run grouping” to apply new thresholds."); qc.setQueryData(["settings"], r); },
    onError: (e) => toast(errMsg(e), true),
  });
  const action = useMutation({
    mutationFn: (path: string) => post<Record<string, unknown>>(path),
    onSuccess: (_r, path) => { toast(path.endsWith("scan") ? "Scan queued." : path.endsWith("regroup") ? "Regrouping queued." : "Retry queued."); qc.invalidateQueries({ queryKey: ["status"] }); },
    onError: (e) => toast(errMsg(e), true),
  });

  if (!s) return <section className="card pad"><Loading /></section>;
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setS({ ...s, [k]: v });
  const w = s.keeper_weights;

  return (
    <section className="card pad stack">
      <h2>Duplicate detection</h2>
      <div className="form-grid">
        <Num label="Re-encode threshold" value={s.reencode_threshold} onChange={(v) => set("reencode_threshold", v)} hint="Max pHash distance (0–64) to call two photos the same picture. Lower = stricter. Default 6." />
        <Num label="Burst threshold" value={s.burst_threshold} onChange={(v) => set("burst_threshold", v)} hint="Max pHash distance for 'similar shots'. Default 14." />
        <Num label="Burst window (s)" value={s.burst_window_seconds} onChange={(v) => set("burst_window_seconds", v)} />
        <Num label="Video duration ± (s)" value={s.video_duration_tolerance} step={0.5} onChange={(v) => set("video_duration_tolerance", v)} />
        <Num label="Video frame threshold" value={s.video_frame_threshold} onChange={(v) => set("video_frame_threshold", v)} />
      </div>
      <h3>Which copy to keep — weights</h3>
      <div className="form-grid">
        {(Object.keys(w) as (keyof typeof w)[]).map((k) => (
          <Num key={k} label={{ exif: "EXIF metadata", gps: "GPS location", resolution: "Resolution", format: "Original format", size: "File size" }[k]}
            value={w[k]} onChange={(v) => set("keeper_weights", { ...w, [k]: v })} />
        ))}
      </div>
      <h3>Face recognition</h3>
      <label className="check">
        <input type="checkbox" checked={s.faces_enabled} onChange={(e) => set("faces_enabled", e.target.checked)} />
        Find and group faces (runs on the NAS; first time downloads ~290 MB of InsightFace models, non-commercial license)
      </label>
      <div className="form-grid">
        <Num label="Recognise as (0–1)" value={s.face_auto_threshold} step={0.01} onChange={(v) => set("face_auto_threshold", v)} hint="How similar a face must be to a named person to be tagged automatically. Higher = fewer mistakes, more to review. Default 0.5." />
        <Num label="Suggest from (0–1)" value={s.face_suggest_threshold} step={0.01} onChange={(v) => set("face_suggest_threshold", v)} hint="Faces at least this similar are offered for review. Default 0.38." />
      </div>
      <h3>Scanning</h3>
      <div className="form-grid">
        <Num label="Scan every (minutes)" value={s.scan_interval_minutes} onChange={(v) => set("scan_interval_minutes", v)} />
        <label className="check" style={{ alignSelf: "end", height: 34 }}>
          <input type="checkbox" checked={s.scans_paused} onChange={(e) => set("scans_paused", e.target.checked)} />
          Pause scheduled scans
        </label>
      </div>
      <h3>Quarantine</h3>
      <div className="form-grid">
        <Num label="Days before purge allowed" value={s.purge_days} onChange={(v) => set("purge_days", v)} />
        <label className="check" style={{ alignSelf: "end", height: 34 }}>
          <input type="checkbox" checked={s.auto_purge} onChange={(e) => set("auto_purge", e.target.checked)} />
          Auto-purge daily
        </label>
      </div>
      <div className="row">
        <button className="btn primary" disabled={save.isPending || JSON.stringify(s) === JSON.stringify(q.data)} onClick={() => save.mutate(s)}>Save settings</button>
        <div className="grow" />
        <button className="btn" onClick={() => action.mutate("/api/admin/scan")}>Scan now</button>
        <button className="btn" onClick={() => action.mutate("/api/admin/regroup")}>Re-run grouping</button>
      </div>
    </section>
  );
}

function SystemStatus() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["status"], queryFn: () => api<Status>("/api/admin/status"), refetchInterval: 5_000 });
  const retry = useMutation({
    mutationFn: () => post<{ files: number }>("/api/admin/retry-errors"),
    onSuccess: (r) => { toast(`Retrying ${r.files} files.`); qc.invalidateQueries({ queryKey: ["status"] }); },
  });
  if (!q.data) return <section className="card pad"><Loading /></section>;
  const p = q.data.files.by_process_state;
  const total = (p.done ?? 0) + (p.pending ?? 0) + (p.error ?? 0);
  const proc = q.data.jobs.process ?? {};
  return (
    <section className="card pad stack" style={{ gridColumn: "1 / -1" }}>
      <h2>System status</h2>
      <div className="row">
        <span className="badge">{total.toLocaleString()} files indexed</span>
        <span className="badge keep">{(p.done ?? 0).toLocaleString()} done</span>
        {(p.pending ?? 0) > 0 && <span className="badge accent">{p.pending.toLocaleString()} pending</span>}
        {(p.error ?? 0) > 0 && <span className="badge delete">{p.error.toLocaleString()} errors</span>}
        <span className="badge">{bytes(q.data.files.bytes)}</span>
        <span className="badge">Processing batches: {proc.queued ?? 0} queued · {proc.running ?? 0} running</span>
        <span className="badge">Workers busy: {q.data.active_workers.join(", ") || "none"}</span>
      </div>
      {total > 0 && <div className="progress"><div style={{ width: `${((p.done ?? 0) + (p.error ?? 0)) / total * 100}%` }} /></div>}
      <table className="tbl">
        <thead><tr><th>Job</th><th>State</th><th>Worker</th><th>Queued</th><th>Finished</th><th>Result</th></tr></thead>
        <tbody>
          {q.data.recent_jobs.map((j) => (
            <tr key={j.id}>
              <td>#{j.id} {j.type}</td>
              <td><span className={"badge " + (j.state === "done" ? "keep" : j.state === "error" ? "delete" : "accent")}>{j.state}</span></td>
              <td className="small">{j.worker ?? "—"}</td>
              <td className="small">{date(j.created_at)}</td>
              <td className="small">{date(j.finished_at)}</td>
              <td className="small muted mono" style={{ maxWidth: 420, wordBreak: "break-all" }}>{j.error ?? (j.result ? JSON.stringify(j.result) : "")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {q.data.file_errors.length > 0 && (
        <details>
          <summary style={{ cursor: "pointer" }}>Files that failed to process ({p.error})</summary>
          <div className="row" style={{ margin: "8px 0" }}>
            <button className="btn sm" onClick={() => retry.mutate()}>Retry all</button>
          </div>
          <table className="tbl"><tbody>
            {q.data.file_errors.map((f) => (
              <tr key={f.id}><td className="mono small">{f.path}</td><td className="small muted">{f.error}</td></tr>
            ))}
          </tbody></table>
        </details>
      )}
    </section>
  );
}
