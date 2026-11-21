import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, post, type QuarantineRow } from "../api";
import ItemModal from "../components/ItemModal";
import { Confirm, Loading, Thumb, useToast } from "../ui";
import { bytes, date } from "../util";

interface QData {
  purge_days: number;
  auto_purge: boolean;
  items: QuarantineRow[];
  total_bytes: number;
  eligible: number;
}

export default function Quarantine() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["quarantine"], queryFn: () => api<QData>("/api/quarantine") });
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [confirmPurge, setConfirmPurge] = useState(false);
  const [view, setView] = useState<number | null>(null);

  const refresh = () => {
    setSelected(new Set());
    qc.invalidateQueries();
  };
  const restore = useMutation({
    mutationFn: (ids: number[]) => post<{ done: number[]; failed: { id: number; error: string }[] }>("/api/quarantine/restore", { ids }),
    onSuccess: (r) => {
      if (r.done.length) toast(`Restored ${r.done.length} to their original folders.`);
      if (r.failed.length) toast(`${r.failed.length} could not be restored: ${r.failed[0].error}`, true);
      refresh();
    },
    onError: (e) => toast(String(e instanceof Error ? e.message : e), true),
  });
  const purge = useMutation({
    mutationFn: () => post<{ done: number[]; failed: unknown[] }>("/api/quarantine/purge", { item_ids: null }),
    onSuccess: (r) => {
      setConfirmPurge(false);
      toast(`Permanently deleted ${r.done.length} items.`);
      refresh();
    },
    onError: (e) => toast(String(e instanceof Error ? e.message : e), true),
  });

  if (q.isLoading || !q.data) return <Loading />;
  const d = q.data;
  const eligibleRows = d.items.filter((r) => r.eligible);
  const toggle = (id: number) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  return (
    <>
      <div className="page-head">
        <h1>Quarantine</h1>
        <span className="muted">
          {d.items.length.toLocaleString()} items · {bytes(d.total_bytes)} · kept {d.purge_days} days before they can be purged
          {d.auto_purge ? " (auto-purge on)" : ""}
        </span>
        <div className="spacer" />
        <button className="btn" disabled={!selected.size || restore.isPending} onClick={() => restore.mutate([...selected])}>
          Restore selected ({selected.size})
        </button>
        <button className="btn danger" disabled={!d.eligible} onClick={() => setConfirmPurge(true)}>
          Purge eligible ({d.eligible})
        </button>
      </div>

      <div className="card">
        {d.items.length === 0 ? (
          <div className="empty">
            <div className="big">🗑️</div>
            Quarantine is empty.
          </div>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ width: 36 }}>
                  <input
                    type="checkbox"
                    aria-label="Select all"
                    checked={selected.size === d.items.length}
                    onChange={() => setSelected(selected.size === d.items.length ? new Set() : new Set(d.items.map((r) => r.item.id)))}
                  />
                </th>
                <th />
                <th>File</th>
                <th>Owner</th>
                <th>Marked / approved</th>
                <th>Quarantined</th>
                <th>Purge after</th>
              </tr>
            </thead>
            <tbody>
              {d.items.map((r) => (
                <tr key={r.item.id}>
                  <td>
                    <input type="checkbox" checked={selected.has(r.item.id)} onChange={() => toggle(r.item.id)} aria-label="Select" />
                  </td>
                  <td style={{ width: 70, cursor: "pointer" }} onClick={() => setView(r.item.id)}>
                    <Thumb file={r.item.primary} size={52} live={r.item.is_live_photo} />
                  </td>
                  <td>
                    <div style={{ fontWeight: 600 }}>{r.item.primary.name}</div>
                    <div className="small faint mono">{r.item.primary.folder}</div>
                  </td>
                  <td>{r.item.owner}</td>
                  <td className="small">
                    {r.marked_by ?? "—"} / {r.approved_by ?? "—"}
                  </td>
                  <td className="small">{date(r.quarantined_at)}</td>
                  <td className="small">
                    {r.eligible ? <span className="badge delete">Eligible now</span> : date(r.purge_after, false)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {confirmPurge && (
        <Confirm
          title={`Permanently delete ${eligibleRows.length} items?`}
          confirmLabel="Delete permanently"
          danger
          busy={purge.isPending}
          onCancel={() => setConfirmPurge(false)}
          onConfirm={() => purge.mutate()}
        >
          <p style={{ marginTop: 0 }}>
            This removes {bytes(eligibleRows.reduce((n, r) => n + r.item.primary.size + (r.item.companion?.size ?? 0), 0))} from the
            NAS for good. These files have been in quarantine for at least {d.purge_days} days.
          </p>
          <div className="banner error small">This can't be undone (unless you have NAS snapshots/backups).</div>
        </Confirm>
      )}
      {view && <ItemModal itemId={view} onClose={() => setView(null)} />}
    </>
  );
}
