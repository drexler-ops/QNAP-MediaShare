import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, faceThumbUrl, post, type PersonDetail } from "../api";
import { useViewer } from "../components/MediaViewer";
import { Loading, Pager, pageCount, Spinner, Thumb, usePageParam, useToast } from "../ui";
import { cls } from "../util";
import { Avatar } from "./People";

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

const PHOTOS = 60;
const FACES = 120;

export default function PersonPage() {
  const { id } = useParams();
  const pid = Number(id);
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const viewer = useViewer();
  const [page, setPage] = usePageParam("page");
  const [fpage, setFpage] = usePageParam("fpage");
  const q = useQuery({
    queryKey: ["person", pid, page, fpage],
    queryFn: () => api<PersonDetail>(`/api/people/${pid}?page=${page}&page_size=${PHOTOS}&faces_page=${fpage}&faces_page_size=${FACES}`),
    placeholderData: (prev) => (prev?.id === pid ? prev : undefined),
    retry: false,
    refetchInterval: (query) => (query.state.data?.regrouping ? 3000 : false), // counts catch up after a change
  });
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
    onSuccess: (r, body) => {
      setEditing(false);
      if (r.merged) {
        toast(`Added to ${body.name} (same name, so the groups were merged).`);
        navigate(`/people/${r.id}`, { replace: true });
      } else if (body.name) toast(q.data?.name ? `Renamed to ${body.name}.` : `Named ${body.name}.`);
      else if (body.hidden !== undefined) toast(body.hidden ? "Hidden from People." : "Unhidden.");
      refresh();
    },
    onError: (e) => {
      toast(errMsg(e), true);
      refresh();
    },
  });
  const merge = useMutation({
    mutationFn: (into: number) => post<{ id: number }>(`/api/people/${pid}/merge`, { into }),
    onSuccess: (r) => {
      const into = names.data?.find((n) => n.id === r.id)?.name;
      toast(into ? `Merged into ${into}.` : "Merged.");
      setMergeInto("");
      refresh();
      navigate(`/people/${r.id}`, { replace: true });
    },
    onError: (e) => {
      toast(errMsg(e), true);
      refresh();
    },
  });
  const faceAction = useMutation({
    mutationFn: ({ path, body }: { path: string; body: unknown }) => post<Record<string, number>>(path, body),
    onSuccess: (_r, v) => {
      const n = (v.body as { face_ids: number[] }).face_ids.length;
      const faces = `${n} face${n === 1 ? "" : "s"}`;
      if (v.path.endsWith("/assign")) toast(`Moved ${faces} to ${moveTo.trim()}.`);
      else if (v.path.endsWith("/unassign")) toast(`Removed ${faces}. They won't be matched to ${q.data?.name ?? "this person"} again.`);
      else if (v.path.endsWith("/ignore")) toast(`Removed ${faces} (not a face).`);
      else toast("Updated.");
      if (v.path.endsWith("/assign")) setMoveTo("");
      refresh();
    },
    onError: (e) => {
      toast(errMsg(e), true);
      refresh();
    },
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
  const toEntry = (x: { id: number; primary: { id: number } }) => ({ item: x.id, file: x.primary.id });
  // The viewer continues through all of this person's photos; closing it lands on the page you ended on.
  const openPhoto = (i: number) =>
    viewer.open(p.items.map(toEntry), i, {
      paging: {
        page,
        pageSize: PHOTOS,
        total: p.items_total,
        fetch: (n) => api<PersonDetail>(`/api/people/${pid}?page=${n}&page_size=${PHOTOS}&faces_page_size=1`).then((d) => d.items.map(toEntry)),
      },
      onClose: (pos) => setPage(Math.floor(pos / PHOTOS) + 1),
    });
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
              <input type="text" list="person-names" value={name} onChange={(e) => setName(e.target.value)} autoFocus aria-label="Name" disabled={patch.isPending} />
              <button className="btn primary sm" disabled={!name.trim() || patch.isPending}>{patch.isPending ? <><Spinner /> Saving…</> : "Save"}</button>
              <button type="button" className="btn sm" onClick={() => setEditing(false)}>Cancel</button>
            </form>
          ) : (
            <h1>{label}{p.hidden && <span className="badge" style={{ marginLeft: 8 }}>hidden</span>}</h1>
          )}
          <div className="small faint">
            {p.photos.toLocaleString()} photos · {p.faces.toLocaleString()} faces
            {p.regrouping && <span className="updating" style={{ marginLeft: 10 }}><Spinner /> Updating…</span>}
          </div>
        </div>
        {!editing && (
          <div className="row">
            <button className="btn" onClick={() => { setName(p.name ?? ""); setEditing(true); }}>{p.name ? "Rename" : "Name"}</button>
            <select value={mergeInto} onChange={(e) => setMergeInto(e.target.value)} aria-label="Merge into">
              <option value="">Merge into…</option>
              {names.data?.filter((n) => n.id !== p.id).map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
            </select>
            {mergeInto && <button className="btn primary" disabled={merge.isPending} onClick={() => merge.mutate(Number(mergeInto))}>{merge.isPending ? "Merging…" : "Merge"}</button>}
            <button className="btn ghost" disabled={patch.isPending} onClick={() => patch.mutate({ hidden: !p.hidden })}>{p.hidden ? "Unhide" : "Hide"}</button>
          </div>
        )}
      </div>
      <datalist id="person-names">{names.data?.map((n) => <option key={n.id} value={n.name} />)}</datalist>

      <div className="seg" style={{ marginBottom: 14 }}>
        <button className={cls(tab === "photos" && "on")} onClick={() => setTab("photos")}>Photos ({p.items_total.toLocaleString()})</button>
        <button className={cls(tab === "faces" && "on")} onClick={() => setTab("faces")}>Faces ({p.faces_total.toLocaleString()})</button>
      </div>

      {tab === "photos" ? (
        <>
          <Pager top page={page} pages={pageCount(p.items_total, PHOTOS)} onPage={setPage} />
          <div className="grid">
            {p.items.map((it, i) => (
              <div key={it.id} className="tile" onClick={() => openPhoto(i)}>
                <Thumb file={it.primary} size="100%" live={it.is_live_photo} />
              </div>
            ))}
          </div>
          <Pager page={page} pages={pageCount(p.items_total, PHOTOS)} onPage={setPage} />
        </>
      ) : (
        <>
          <Pager top page={fpage} pages={pageCount(p.faces_total, FACES)} onPage={(n) => { setFpage(n); setSelected(new Set()); }} />
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
          <Pager page={fpage} pages={pageCount(p.faces_total, FACES)} onPage={(n) => { setFpage(n); setSelected(new Set()); }} />
        </>
      )}
    </>
  );
}
