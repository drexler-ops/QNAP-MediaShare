import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, post, type Me, type PendingRow } from "../api";
import { useViewer, type ViewerEntry } from "../components/MediaViewer";
import { Confirm, Loading, Thumb, useToast } from "../ui";
import { bytes, date, dims } from "../util";

interface ApproveResult {
  done: number[];
  failed: { id: number; error: string }[];
}

export default function Pending({ me }: { me: Me }) {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["pending"], queryFn: () => api<{ to_approve: PendingRow[]; waiting_on_others: PendingRow[] }>("/api/pending") });
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [confirm, setConfirm] = useState<number[] | null>(null);
  const viewer = useViewer();
  const compare = (r: PendingRow, which: "kept" | "item") => {
    const entries: ViewerEntry[] = [];
    if (r.kept) entries.push({ item: r.kept.id, file: r.kept.primary.id });
    entries.push({ item: r.item.id, file: r.item.primary.id });
    viewer.open(entries, which === "kept" ? 0 : entries.length - 1);
  };

  const refresh = () => {
    setSelected(new Set());
    qc.invalidateQueries({ queryKey: ["pending"] });
    qc.invalidateQueries({ queryKey: ["pending-count"] });
    qc.invalidateQueries({ queryKey: ["summary"] });
    qc.invalidateQueries({ queryKey: ["group"] });
  };

  const approve = useMutation({
    mutationFn: (ids: number[]) => post<ApproveResult>("/api/decisions/approve", { ids }),
    onSuccess: (r) => {
      setConfirm(null);
      if (r.done.length) toast(`Moved ${r.done.length} to quarantine. Restore them any time from Quarantine.`);
      if (r.failed.length) toast(`${r.failed.length} could not be moved: ${r.failed[0].error}`, true);
      refresh();
    },
    onError: (e) => toast(String(e instanceof Error ? e.message : e), true),
  });
  const reject = useMutation({
    mutationFn: (ids: number[]) => post("/api/decisions/reject", { ids }),
    onSuccess: () => { toast("Kept. The group is back in the review queue."); refresh(); },
    onError: (e) => toast(String(e instanceof Error ? e.message : e), true),
  });
  const cancel = useMutation({
    mutationFn: (ids: number[]) => post("/api/decisions/cancel", { ids }),
    onSuccess: () => { toast("Deletion request withdrawn."); refresh(); },
    onError: (e) => toast(String(e instanceof Error ? e.message : e), true),
  });

  if (q.isLoading || !q.data) return <Loading />;
  const rows = q.data.to_approve;
  const toggle = (id: number) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const allSelected = rows.length > 0 && selected.size === rows.length;
  const confirmRows = confirm ? rows.filter((r) => confirm.includes(r.decision_id)) : [];

  return (
    <>
      <div className="page-head">
        <h1>Approvals</h1>
        <span className="muted">Files other people (or you) marked for deletion that need {me.name}'s OK</span>
      </div>

      <div className="card" style={{ marginBottom: 24 }}>
        <div className="row" style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)" }}>
          <label className="check">
            <input
              type="checkbox"
              checked={allSelected}
              onChange={() => setSelected(allSelected ? new Set() : new Set(rows.map((r) => r.decision_id)))}
              disabled={!rows.length}
            />
            <strong>Needs your approval ({rows.length})</strong>
          </label>
          <div className="grow" />
          <button className="btn sm" disabled={!selected.size || reject.isPending} onClick={() => reject.mutate([...selected])}>
            Keep selected
          </button>
          <button className="btn sm danger" disabled={!selected.size} onClick={() => setConfirm([...selected])}>
            Approve selected ({selected.size})
          </button>
        </div>
        {rows.length === 0 ? (
          <div className="empty">
            <div className="big">✅</div>
            Nothing waiting for you.
          </div>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ width: 36 }} />
                <th>Keeping</th>
                <th>To delete</th>
                <th>Details</th>
                <th>Marked by</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.decision_id}>
                  <td>
                    <input type="checkbox" checked={selected.has(r.decision_id)} onChange={() => toggle(r.decision_id)} aria-label="Select" />
                  </td>
                  <td>
                    {r.kept ? (
                      <div className="row" style={{ cursor: "pointer" }} onClick={() => compare(r, "kept")} title="View (← → to compare with the copy to delete)">
                        <Thumb file={r.kept.primary} size={72} live={r.kept.is_live_photo} />
                        <div className="small">
                          <div>{r.kept.primary.name}</div>
                          <div className="faint">{r.kept.owner} · {dims(r.kept.primary)}</div>
                        </div>
                      </div>
                    ) : (
                      <span className="faint">—</span>
                    )}
                  </td>
                  <td>
                    <div className="row" style={{ cursor: "pointer" }} onClick={() => compare(r, "item")} title="View (← → to compare with the kept copy)">
                      <Thumb file={r.item.primary} size={72} live={r.item.is_live_photo} />
                      <div className="small">
                        <div style={{ fontWeight: 600 }}>{r.item.primary.name}</div>
                        <div className="faint mono">{r.item.primary.folder}</div>
                      </div>
                    </div>
                  </td>
                  <td className="small muted">
                    {r.item.primary.ext.toUpperCase()} · {dims(r.item.primary)} · {bytes(r.item.primary.size)}
                    <br />
                    {r.item.primary.has_exif ? "Has EXIF" : "No EXIF"}
                    {r.item.primary.has_gps ? " · GPS" : ""}
                    {r.item.is_live_photo ? " · Live Photo (both parts)" : ""}
                  </td>
                  <td className="small">
                    {r.marked_by}
                    <div className="faint">{date(r.marked_at)}</div>
                  </td>
                  <td>
                    <div className="row" style={{ justifyContent: "flex-end" }}>
                      {r.group_id && <Link className="btn sm ghost" to={`/review?group=${r.group_id}&status=all`}>Group</Link>}
                      <button className="btn sm" onClick={() => reject.mutate([r.decision_id])}>Keep</button>
                      <button className="btn sm danger" onClick={() => setConfirm([r.decision_id])}>Approve</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <h2>Waiting on others ({q.data.waiting_on_others.length})</h2>
      <p className="muted small" style={{ marginTop: -6 }}>Files you marked that belong to someone else.</p>
      <div className="card">
        {q.data.waiting_on_others.length === 0 ? (
          <div className="empty small">Nothing outstanding.</div>
        ) : (
          <table className="tbl">
            <tbody>
              {q.data.waiting_on_others.map((r) => (
                <tr key={r.decision_id}>
                  <td style={{ width: 80 }}>
                    <Thumb file={r.item.primary} size={56} />
                  </td>
                  <td>
                    <div style={{ fontWeight: 600 }}>{r.item.primary.name}</div>
                    <div className="small faint mono">{r.item.primary.folder}</div>
                  </td>
                  <td className="small">Waiting on <strong>{r.item.owner}</strong> · marked {date(r.marked_at)}</td>
                  <td style={{ textAlign: "right" }}>
                    <button className="btn sm" onClick={() => cancel.mutate([r.decision_id])}>Withdraw</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {confirm && (
        <Confirm
          title={`Move ${confirm.length} ${confirm.length === 1 ? "file" : "files"} to quarantine?`}
          confirmLabel="Approve & move to quarantine"
          danger
          busy={approve.isPending}
          onCancel={() => setConfirm(null)}
          onConfirm={() => approve.mutate(confirm)}
        >
          <p style={{ marginTop: 0 }}>
            {bytes(confirmRows.reduce((n, r) => n + r.item.primary.size + (r.item.companion?.size ?? 0), 0))} will be moved out of
            your folders into <code>.quarantine</code> on the NAS. Nothing is permanently deleted yet. You can restore files from the
            Quarantine page until they're purged.
          </p>
        </Confirm>
      )}
    </>
  );
}
