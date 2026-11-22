import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, post, type AlbumDetail, type Me, type ShareLinkInfo, type UserRow } from "../api";
import { useViewer } from "../components/MediaViewer";
import { Confirm, Loading, Modal, Thumb, useAppConfig, useLongPress, useToast } from "../ui";
import { cls, date } from "../util";

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

export default function AlbumPage({ me }: { me: Me }) {
  const { id } = useParams();
  const albumId = Number(id);
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const viewer = useViewer();
  const q = useQuery({ queryKey: ["album", albumId], queryFn: () => api<AlbumDetail>(`/api/albums/${albumId}`), retry: false });
  const a = q.data;
  const canEdit = a?.role === "owner" || a?.role === "editor";
  const isOwner = a?.role === "owner";

  const [order, setOrder] = useState<number[]>([]);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [sharing, setSharing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const longPress = useLongPress((idx) => {
    if (!(a?.role === "owner" || a?.role === "editor")) return;
    setSelecting(true);
    const iid = order[idx];
    setSelected((cur) => new Set(cur).add(iid));
  });

  useEffect(() => {
    if (a) setOrder(a.items.map((i) => i.id));
  }, [a]);
  const byId = useMemo(() => new Map((a?.items ?? []).map((i) => [i.id, i])), [a]);
  const items = order.map((i) => byId.get(i)).filter((x): x is NonNullable<typeof x> => !!x);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["album", albumId] });
    qc.invalidateQueries({ queryKey: ["albums"] });
  };
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<AlbumDetail>(`/api/albums/${albumId}`, { method: "PATCH", json: body }),
    onSuccess: () => { setEditing(false); refresh(); },
    onError: (e) => toast(errMsg(e), true),
  });
  const saveOrder = useMutation({
    mutationFn: (ids: number[]) => api(`/api/albums/${albumId}/order`, { method: "PUT", json: { item_ids: ids } }),
    onError: (e) => { toast(errMsg(e), true); refresh(); },
  });
  const remove = useMutation({
    mutationFn: (ids: number[]) => post<{ removed: number }>(`/api/albums/${albumId}/items/remove`, { item_ids: ids }),
    onSuccess: (r) => { toast(`Removed ${r.removed} from the album. The photos themselves are untouched.`); setSelected(new Set()); refresh(); },
    onError: (e) => toast(errMsg(e), true),
  });
  const del = useMutation({
    mutationFn: () => api(`/api/albums/${albumId}`, { method: "DELETE" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["albums"] }); toast("Album deleted. No photos were deleted."); navigate("/albums"); },
    onError: (e) => toast(errMsg(e), true),
  });

  if (q.isLoading) return <Loading />;
  if (!a) {
    return (
      <div className="card empty">
        <h2>Album not found</h2>
        <p>It may have been deleted, or it isn't shared with you. <Link to="/albums">Back to albums</Link></p>
      </div>
    );
  }

  const open = (idx: number) => viewer.open(items.map((i) => ({ item: i.id, file: i.primary.id })), idx);
  const toggle = (iid: number) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(iid)) n.delete(iid);
      else n.add(iid);
      return n;
    });
  const drop = (to: number) => {
    if (dragFrom === null || dragFrom === to) return;
    const next = [...order];
    const [moved] = next.splice(dragFrom, 1);
    next.splice(to, 0, moved);
    setOrder(next);
    setDragFrom(null);
    saveOrder.mutate(next);
  };

  return (
    <>
      <div className="page-head" style={{ alignItems: "flex-start" }}>
        <div className="grow">
          <div className="small muted"><Link to="/albums">Albums</Link> ›</div>
          {editing ? (
            <form className="stack" style={{ gap: 8, maxWidth: 560 }} onSubmit={(e) => { e.preventDefault(); patch.mutate({ title, description: desc }); }}>
              <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus aria-label="Album name" />
              <input type="text" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Description (optional)" aria-label="Description" />
              <div className="row">
                <button className="btn primary sm" disabled={!title.trim()}>Save</button>
                <button type="button" className="btn sm" onClick={() => setEditing(false)}>Cancel</button>
              </div>
            </form>
          ) : (
            <>
              <h1>{a.title}</h1>
              {a.description && <p className="muted" style={{ margin: "4px 0 0" }}>{a.description}</p>}
              <div className="small faint" style={{ marginTop: 4 }}>
                {a.count} {a.count === 1 ? "item" : "items"} · {a.role === "owner" ? "your album" : `${a.owner}'s album · you can ${a.role === "editor" ? "edit" : "view"}`}
                {a.members.length > 0 && ` · shared with ${a.members.map((m) => m.name).join(", ")}`}
                {a.active_links > 0 && " · public link active"}
              </div>
            </>
          )}
        </div>
        {!editing && (
          <div className="row">
            {isOwner && <button className="btn" onClick={() => { setTitle(a.title); setDesc(a.description ?? ""); setEditing(true); }}>Rename</button>}
            {canEdit && a.count > 0 && (
              <button className={cls("btn", selecting && "primary")} onClick={() => { setSelecting((v) => !v); setSelected(new Set()); }}>
                {selecting ? "Done" : "Select"}
              </button>
            )}
            {isOwner && <button className="btn primary" onClick={() => setSharing(true)}>Share</button>}
            {isOwner && <button className="btn ghost" onClick={() => setConfirmDelete(true)}>Delete album</button>}
          </div>
        )}
      </div>

      {selecting && (
        <div className="card pad row select-bar">
          <strong>{selected.size} selected</strong>
          <button className="btn sm" onClick={() => setSelected(new Set(order))}>Select all</button>
          <button className="btn sm" disabled={!selected.size} onClick={() => setSelected(new Set())}>Clear</button>
          <div className="grow" />
          <button className="btn sm" disabled={selected.size !== 1} onClick={() => patch.mutate({ cover_item_id: [...selected][0] })}>★ Set as cover</button>
          <button className="btn sm danger" disabled={!selected.size || remove.isPending} onClick={() => remove.mutate([...selected])}>Remove from album</button>
        </div>
      )}

      {items.length === 0 ? (
        <div className="card empty">
          <div className="big">🖼️</div>
          This album is empty. In <Link to="/browse">Browse</Link>, click <strong>Select</strong>, pick photos and choose <strong>Add to album</strong>, or press <kbd>A</kbd> in the viewer.
        </div>
      ) : (
        <>
          {canEdit && !selecting && <p className="small faint" style={{ marginTop: -6 }}>Drag photos to reorder.</p>}
          <div className="grid">
            {items.map((it, idx) => (
              <div
                key={it.id}
                className={cls("tile", selecting && selected.has(it.id) && "selected", dragFrom === idx && "dragging")}
                draggable={canEdit && !selecting}
                onDragStart={() => setDragFrom(idx)}
                onDragOver={(e) => canEdit && e.preventDefault()}
                onDrop={() => drop(idx)}
                onDragEnd={() => setDragFrom(null)}
                {...longPress.bind(idx)}
                onClick={() => {
                  if (longPress.consumedClick()) return;
                  if (selecting) toggle(it.id);
                  else open(idx);
                }}
              >
                <div style={{ position: "relative" }}>
                  <Thumb file={it.primary} size="100%" live={it.is_live_photo} dim={it.primary.status !== "active" && it.primary.status !== "pending_delete"} />
                  {a.cover_item_id === it.id && <span className="badge accent tile-flag">★ Cover</span>}
                  {selecting && <span className="tile-check">{selected.has(it.id) ? "✓" : ""}</span>}
                </div>
                <div className="tile-meta">{date(it.primary.taken_at, false)}</div>
              </div>
            ))}
          </div>
        </>
      )}

      {sharing && <ShareDialog album={a} me={me} onClose={() => setSharing(false)} onChanged={refresh} />}
      {confirmDelete && (
        <Confirm title={`Delete “${a.title}”?`} confirmLabel="Delete album" danger busy={del.isPending} onCancel={() => setConfirmDelete(false)} onConfirm={() => del.mutate()}>
          <p style={{ marginTop: 0 }}>
            The album and its share links go away. <strong>The photos themselves are not deleted</strong>; they stay in everyone's folders.
          </p>
        </Confirm>
      )}
    </>
  );
}

const EXPIRY = [
  { label: "Never expires", days: null },
  { label: "1 day", days: 1 },
  { label: "7 days", days: 7 },
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
];

function ShareDialog({ album, me, onClose, onChanged }: { album: AlbumDetail; me: Me; onClose: () => void; onChanged: () => void }) {
  const toast = useToast();
  const cfg = useAppConfig();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<{ users: UserRow[] }>("/api/users") });
  const [members, setMembers] = useState(album.members.map((m) => ({ user_id: m.user_id, role: m.role })));
  const [addUser, setAddUser] = useState("");
  const [expires, setExpires] = useState<number | null>(7);
  const [password, setPassword] = useState("");
  const [allowDownload, setAllowDownload] = useState(false);
  const [links, setLinks] = useState<ShareLinkInfo[]>(album.links);

  const fullUrl = (l: ShareLinkInfo) => (/^https?:/.test(l.url) ? l.url : `${window.location.origin}${l.url}`);
  const copy = async (l: ShareLinkInfo) => {
    try {
      await navigator.clipboard.writeText(fullUrl(l));
      toast("Link copied.");
    } catch {
      toast(fullUrl(l));
    }
  };

  const saveMembers = useMutation({
    mutationFn: (m: typeof members) => api<AlbumDetail>(`/api/albums/${album.id}/members`, { method: "PUT", json: { members: m } }),
    onSuccess: (r) => { setMembers(r.members.map((x) => ({ user_id: x.user_id, role: x.role }))); onChanged(); },
    onError: (e) => toast(errMsg(e), true),
  });
  const createLink = useMutation({
    mutationFn: () => post<ShareLinkInfo>(`/api/albums/${album.id}/links`, { expires_days: expires, password: password || null, allow_download: allowDownload }),
    onSuccess: (l) => { setLinks((x) => [l, ...x]); setPassword(""); onChanged(); copy(l); },
    onError: (e) => toast(errMsg(e), true),
  });
  const revoke = useMutation({
    mutationFn: (l: ShareLinkInfo) => post<ShareLinkInfo>(`/api/albums/${album.id}/links/${l.id}/revoke`),
    onSuccess: (l) => { setLinks((x) => x.map((y) => (y.id === l.id ? l : y))); onChanged(); toast("Link revoked. It stops working immediately."); },
    onError: (e) => toast(errMsg(e), true),
  });

  const others = (users.data?.users ?? []).filter((u) => u.id !== me.id && u.is_active && !members.some((m) => m.user_id === u.id));
  const name = (uid: number) => users.data?.users.find((u) => u.id === uid)?.name ?? `#${uid}`;

  return (
    <Modal title={`Share “${album.title}”`} onClose={onClose} wide>
      <div className="share-cols">
        <section>
          <h3>People in this app</h3>
          <p className="small muted" style={{ marginTop: 0 }}>They see it under Albums → Shared with me. Editors can add, remove and reorder photos.</p>
          {members.length === 0 && <p className="small faint">Not shared with anyone yet.</p>}
          {members.map((m) => (
            <div key={m.user_id} className="row" style={{ marginBottom: 6 }}>
              <span className="grow">{name(m.user_id)}</span>
              <select value={m.role} onChange={(e) => saveMembers.mutate(members.map((x) => (x.user_id === m.user_id ? { ...x, role: e.target.value as "viewer" | "editor" } : x)))}>
                <option value="viewer">Can view</option>
                <option value="editor">Can edit</option>
              </select>
              <button className="btn sm ghost" onClick={() => saveMembers.mutate(members.filter((x) => x.user_id !== m.user_id))} aria-label={`Remove ${name(m.user_id)}`}>✕</button>
            </div>
          ))}
          <div className="row" style={{ marginTop: 8 }}>
            <select value={addUser} onChange={(e) => setAddUser(e.target.value)} className="grow" aria-label="Add person">
              <option value="">Add a person…</option>
              {others.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
            <button className="btn sm" disabled={!addUser} onClick={() => { saveMembers.mutate([...members, { user_id: Number(addUser), role: "viewer" }]); setAddUser(""); }}>Add</button>
          </div>
        </section>

        <section>
          <h3>Public link (anyone with the link)</h3>
          <p className="small muted" style={{ marginTop: 0 }}>
            Opens a read-only page with just this album: no sign-in, and no access to anything else. Owner names, folders and locations are
            never shown.
          </p>
          {!cfg.share_base_url && (
            <div className="banner info small" style={{ marginBottom: 10 }}>
              Public access isn't set up yet, so links only work on your home network for now. Once it's enabled, existing links work
              from anywhere.
            </div>
          )}
          <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <label className="field">
              Expires
              <select value={expires ?? ""} onChange={(e) => setExpires(e.target.value ? Number(e.target.value) : null)}>
                {EXPIRY.map((x) => <option key={x.label} value={x.days ?? ""}>{x.label}</option>)}
              </select>
            </label>
            <label className="field">
              Password (optional)
              <input type="text" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="none" autoComplete="off" />
            </label>
          </div>
          <label className="check" style={{ margin: "10px 0" }}>
            <input type="checkbox" checked={allowDownload} onChange={(e) => setAllowDownload(e.target.checked)} />
            Allow downloading originals (includes their metadata, e.g. location)
          </label>
          <button className="btn primary" disabled={createLink.isPending} onClick={() => createLink.mutate()}>Create link &amp; copy</button>

          {links.length > 0 && (
            <div className="stack" style={{ marginTop: 14, gap: 8 }}>
              {links.map((l) => (
                <div key={l.id} className={cls("link-row", l.state !== "active" && "inactive")}>
                  <div className="grow" style={{ minWidth: 0 }}>
                    <div className="mono small" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{fullUrl(l)}</div>
                    <div className="row small" style={{ gap: 4, marginTop: 4 }}>
                      <span className={cls("badge", l.state === "active" ? "keep" : "delete")}>{l.state}</span>
                      <span className="badge">{l.expires_at ? `until ${date(l.expires_at, false)}` : "no expiry"}</span>
                      {l.has_password && <span className="badge">🔒 password</span>}
                      <span className="badge">{l.allow_download ? "downloads on" : "view only"}</span>
                      <span className="badge">{l.view_count} views</span>
                    </div>
                  </div>
                  {l.state === "active" && (
                    <div className="row" style={{ flexWrap: "nowrap" }}>
                      <button className="btn sm" onClick={() => copy(l)}>Copy</button>
                      <button className="btn sm danger" onClick={() => revoke.mutate(l)}>Revoke</button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </Modal>
  );
}
