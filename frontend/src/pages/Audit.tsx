import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, qs, type Facets } from "../api";
import { Loading } from "../ui";
import { date } from "../util";

interface AuditRow {
  id: number;
  ts: string;
  user: string;
  action: string;
  target_type: string | null;
  target_id: number | null;
  details: Record<string, unknown> | null;
}

const ACTION_LABEL: Record<string, string> = {
  mark_delete: "Marked for deletion",
  mark_keep: "Marked keep",
  mark_skip: "Skipped",
  approve_quarantine: "Approved → quarantine",
  quarantine_failed: "Quarantine failed",
  reject_delete: "Rejected deletion",
  cancel_delete: "Withdrew deletion",
  restore: "Restored",
  purge: "Purged permanently",
  bulk_mark_exact: "Bulk-marked exact copies",
  dismiss_group: "Dismissed group",
  reopen_group: "Reopened group",
};

function summarize(d: Record<string, unknown> | null): string {
  if (!d) return "";
  return Object.entries(d)
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(" · ");
}

export default function Audit() {
  const [user, setUser] = useState("");
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);
  const facets = useQuery({ queryKey: ["facets"], queryFn: () => api<Facets>("/api/facets") });
  const q = useQuery({
    queryKey: ["audit", user, action, page],
    queryFn: () => api<{ total: number; rows: AuditRow[]; actions: string[] }>(`/api/audit${qs({ user_id: user, action, page, page_size: 100 })}`),
    placeholderData: (prev) => prev,
  });
  const pages = q.data ? Math.max(1, Math.ceil(q.data.total / 100)) : 1;

  return (
    <>
      <div className="page-head">
        <h1>Audit log</h1>
        <span className="muted">Every decision, approval, restore and purge, with who did it</span>
        <div className="spacer" />
        <select value={user} onChange={(e) => { setUser(e.target.value); setPage(1); }} aria-label="User">
          <option value="">Everyone</option>
          {facets.data?.owners.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
        </select>
        <select value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }} aria-label="Action">
          <option value="">All actions</option>
          {q.data?.actions.map((a) => <option key={a} value={a}>{ACTION_LABEL[a] ?? a}</option>)}
        </select>
      </div>
      <div className="card">
        {q.isLoading ? (
          <Loading />
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>When</th>
                <th>Who</th>
                <th>Action</th>
                <th>Target</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {q.data?.rows.map((r) => (
                <tr key={r.id}>
                  <td className="small" style={{ whiteSpace: "nowrap" }}>{date(r.ts)}</td>
                  <td>{r.user}</td>
                  <td>
                    <span className={"badge " + (r.action.includes("purge") || r.action.includes("approve") ? "delete" : r.action === "restore" ? "keep" : "")}>
                      {ACTION_LABEL[r.action] ?? r.action}
                    </span>
                  </td>
                  <td className="small">{r.target_type ? `${r.target_type} #${r.target_id}` : "—"}</td>
                  <td className="small muted mono" style={{ maxWidth: 520, wordBreak: "break-all" }}>{summarize(r.details)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {pages > 1 && (
        <div className="row" style={{ justifyContent: "center", marginTop: 16 }}>
          <button className="btn sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ Newer</button>
          <span className="muted small">Page {page} of {pages}</span>
          <button className="btn sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Older ›</button>
        </div>
      )}
    </>
  );
}
