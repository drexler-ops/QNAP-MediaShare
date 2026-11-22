import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, post, type AlbumDetail, type AlbumSummary } from "../api";
import { Loading, Modal, useToast } from "../ui";
import { thumbUrl } from "../util";

/** "Add to album" dialog: pick an album you can edit, or create a new one with these items. */
export default function AlbumPicker({ itemIds, onClose, onDone, z }: { itemIds: number[]; onClose: () => void; onDone?: () => void; z?: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [title, setTitle] = useState("");
  const albums = useQuery({ queryKey: ["albums", "editable"], queryFn: () => api<{ albums: AlbumSummary[] }>("/api/albums/editable") });
  const n = itemIds.length;
  const label = `${n} ${n === 1 ? "item" : "items"}`;

  const finish = (msg: string) => {
    toast(msg);
    qc.invalidateQueries({ queryKey: ["albums"] });
    qc.invalidateQueries({ queryKey: ["album"] });
    qc.invalidateQueries({ queryKey: ["item-albums"] });
    onDone?.();
    onClose();
  };
  const addTo = useMutation({
    mutationFn: (a: AlbumSummary) => post<{ added: number }>(`/api/albums/${a.id}/items`, { item_ids: itemIds }).then((r) => ({ r, a })),
    onSuccess: ({ r, a }) => finish(r.added ? `Added ${r.added} to “${a.title}”.` : `Already in “${a.title}”.`),
    onError: (e) => toast(String(e instanceof Error ? e.message : e), true),
  });
  const create = useMutation({
    mutationFn: () => post<AlbumDetail>("/api/albums", { title: title.trim(), item_ids: itemIds }),
    onSuccess: (a) => finish(`Created “${a.title}” with ${label}.`),
    onError: (e) => toast(String(e instanceof Error ? e.message : e), true),
  });

  return (
    <Modal title={`Add ${label} to an album`} onClose={onClose} z={z}>
      <form className="row" style={{ marginBottom: 14, flexWrap: "nowrap" }} onSubmit={(e) => { e.preventDefault(); if (title.trim()) create.mutate(); }}>
        <input type="text" placeholder="New album name" value={title} onChange={(e) => setTitle(e.target.value)} className="grow" autoFocus />
        <button className="btn primary" disabled={!title.trim() || create.isPending}>Create</button>
      </form>
      {albums.isLoading ? (
        <Loading />
      ) : !albums.data?.albums.length ? (
        <p className="muted small" style={{ margin: 0 }}>No albums yet. Name a new one above.</p>
      ) : (
        <div className="picker-list">
          {albums.data.albums.map((a) => (
            <button key={a.id} className="picker-row" disabled={addTo.isPending} onClick={() => addTo.mutate(a)}>
              <span className="thumb" style={{ width: 44, height: 44 }}>
                {a.cover_file_id ? <img src={thumbUrl(a.cover_file_id)} alt="" /> : null}
              </span>
              <span className="grow" style={{ textAlign: "left" }}>
                <strong>{a.title}</strong>
                <span className="small muted" style={{ display: "block" }}>
                  {a.count} items{a.role !== "owner" ? ` · ${a.owner}'s album` : ""}
                </span>
              </span>
              <span className="faint">＋</span>
            </button>
          ))}
        </div>
      )}
    </Modal>
  );
}
