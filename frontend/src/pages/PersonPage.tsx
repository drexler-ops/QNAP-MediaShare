import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, faceThumbUrl, post, type PersonDetail } from "../api";
import { useViewer } from "../components/MediaViewer";
import { Loading, Thumb, useToast } from "../ui";
import { cls } from "../util";
import { Avatar } from "./People";

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

export default function PersonPage() {
  const { id } = useParams();
  const pid = Number(id);
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const viewer = useViewer();
  const q = useQuery({ queryKey: ["person", pid], queryFn: () => api<PersonDetail>(`/api/people/${pid}`), retry: false });
  const names = useQuery({ queryKey: ["people-names"], queryFn: () => api<{ id: number; name: string }[]>("/api/people/names") });
  const [tab, setTab] = useState<"photos" | "faces">("photos");
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [mergeInto, setMergeInto] = useState("");
  const [moveTo, setMoveTo] = useState("");

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["person", pid] });
    qc.invalidateQueries({ queryKey: ["people"] });
    qc.invalidateQueries({ queryKey: ["people-names"] });
    setSelected(new Set());
  };
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<{ id: number; merged: boolean }>(`/api/people/${pid}`, { method: "PATCH", json: body }),
    onSuccess: (r) => {
      setEditing(false);
      if (r.merged) {
        toast("Merged with the existing person of that name.");
        navigate(`/people/${r.id}`, { replace: true });
      }
      refresh();
    },
    onError: (e) => toast(errMsg(e), true),
  });
  const merge = useMutation({
    mutationFn: (into: number) => post<{ id: number }>(`/api/people/${pid}/merge`, { into }),
    onSuccess: (r) => { toast("Merged."); refresh(); navigate(`/people/${r.id}`, { replace: true }); },
    onError: (e) => toast(errMsg(e), true),
  });
  const faceAction = useMutation({
    mutationFn: ({ path, body }: { path: string; body: unknown }) => post<Record<string, number>>(path, body),
    onSuccess: () => { toast("Updated. Everyone's photos will be re-checked in the background."); refresh(); },
    onError: (e) => toast(errMsg(e), true),
  });

  if (q.isLoading) return <Loading />;
  const p = q.data;
  if (!p) {
    return (
      <div className="card empty">
        <h2>Not found</h2>
        <p>This group may have been merged or regrouped. <Link to="/people">Back to People</Link></p>
      </div>
    );
  }
  const label = p.name ?? "Unnamed person";
  const ids = [...selected];
  const toggle = (fid: number) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(fid)) n.delete(fid);
      else n.add(fid);
      return n;
    });

  return (
    <>
      <div className="page-head" style={{ alignItems: "center" }}>
        <Avatar person={p} size={72} />
        <div className="grow">
          <div className="small muted"><Link to="/people">People</Link> ›</div>
          {editing ? (
            <form className="row" onSubmit={(e) => { e.preventDefault(); if (name.trim()) patch.mutate({ name: name.trim() }); }}>
              <input type="text" list="person-names" value={name} onChange={(e) => setName(e.target.value)} autoFocus aria-label="Name" />
              <button className="btn primary sm" disabled={!name.trim()}>Save</button>
              <button type="button" className="btn sm" onClick={() => setEditing(false)}>Cancel</button>
            </form>
          ) : (
            <h1>{label}{p.hidden && <span className="badge" style={{ marginLeft: 8 }}>hidden</span>}</h1>
          )}
          <div className="small faint">{p.photos.toLocaleString()} photos · {p.faces.toLocaleString()} faces</div>
        </div>
        {!editing && (
          <div className="row">
            <button className="btn" onClick={() => { setName(p.name ?? ""); setEditing(true); }}>{p.name ? "Rename" : "Name"}</button>
            <select value={mergeInto} onChange={(e) => setMergeInto(e.target.value)} aria-label="Merge into">
              <option value="">Merge into…</option>
              {names.data?.filter((n) => n.id !== p.id).map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
            </select>
            {mergeInto && <button className="btn primary" onClick={() => merge.mutate(Number(mergeInto))}>Merge</button>}
            <button className="btn ghost" onClick={() => patch.mutate({ hidden: !p.hidden })}>{p.hidden ? "Unhide" : "Hide"}</button>
          </div>
        )}
      </div>
      <datalist id="person-names">{names.data?.map((n) => <option key={n.id} value={n.name} />)}</datalist>

      <div className="seg" style={{ marginBottom: 14 }}>
        <button className={cls(tab === "photos" && "on")} onClick={() => setTab("photos")}>Photos ({p.items.length})</button>
        <button className={cls(tab === "faces" && "on")} onClick={() => setTab("faces")}>Faces ({p.face_list.length})</button>
      </div>

      {tab === "photos" ? (
        <div className="grid">
          {p.items.map((it, i) => (
            <div key={it.id} className="tile" onClick={() => viewer.open(p.items.map((x) => ({ item: x.id, file: x.primary.id })), i)}>
              <Thumb file={it.primary} size="100%" live={it.is_live_photo} />
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="card pad row select-bar">
            <strong>{selected.size} selected</strong>
            <span className="small faint">Select faces that aren't {label} to fix them</span>
            <div className="grow" />
            <button className="btn sm" disabled={!selected.size} onClick={() => faceAction.mutate({ path: "/api/faces/unassign", body: { face_ids: ids } })}>
              Not {p.name ?? "this person"}
            </button>
            <form className="row" style={{ flexWrap: "nowrap", gap: 4 }} onSubmit={(e) => { e.preventDefault(); if (moveTo.trim()) faceAction.mutate({ path: "/api/faces/assign", body: { face_ids: ids, name: moveTo.trim() } }); }}>
              <input type="text" list="person-names" placeholder="Move to…" value={moveTo} onChange={(e) => setMoveTo(e.target.value)} style={{ width: 130 }} aria-label="Move to person" />
              <button className="btn sm" disabled={!selected.size || !moveTo.trim()}>Move</button>
            </form>
            <button className="btn sm danger" disabled={!selected.size} onClick={() => faceAction.mutate({ path: "/api/faces/ignore", body: { face_ids: ids } })}>Not a face</button>
          </div>
          <div className="face-grid">
            {p.face_list.map((f) => (
              <button key={f.face_id} className={cls("face-tile", selected.has(f.face_id) && "selected")} onClick={() => toggle(f.face_id)} title={f.assignment === "auto" ? `Recognised automatically (${Math.round((f.similarity ?? 0) * 100)}%)` : "Confirmed"}>
                <img src={faceThumbUrl(f.face_id)} alt="" loading="lazy" />
                {f.assignment === "auto" && <span className="face-auto">auto</span>}
                <span className="tile-check">{selected.has(f.face_id) ? "✓" : ""}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </>
  );
}
