import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api, post, qs, type Facets, type GroupCard, type GroupType, type Summary } from "../api";
import { Confirm, Loading, Thumb, useToast } from "../ui";
import { bytes, cls, TYPE_HINT, TYPE_LABEL } from "../util";

const TYPES: GroupType[] = ["exact", "reencode", "video", "burst"];

interface Status {
  files: { by_process_state: Record<string, number>; by_status: Record<string, number>; kinds: Record<string, number>; bytes: number };
  jobs: Record<string, Record<string, number>>;
  active_workers: string[];
}

interface BulkPreview {
  groups: number;
  files: number;
  reclaim_bytes: number;
  by_owner: Record<string, number>;
  sample: { group_id: number; keep_file_id: number; drop_file_ids: number[] }[];
}

export default function Overview() {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [type, setType] = useState<GroupType | "">("");
  const [status, setStatus] = useState("open");
  const [owner, setOwner] = useState("");
  const [page, setPage] = useState(1);
  const [bulkOpen, setBulkOpen] = useState(false);

  const summary = useQuery({ queryKey: ["summary"], queryFn: () => api<Summary>("/api/groups/summary"), refetchInterval: 15_000 });
  const sys = useQuery({ queryKey: ["status"], queryFn: () => api<Status>("/api/admin/status"), refetchInterval: 10_000 });
  const facets = useQuery({ queryKey: ["facets"], queryFn: () => api<Facets>("/api/facets") });
  const groups = useQuery({
    queryKey: ["groups", type, status, owner, page],
    queryFn: () =>
      api<{ total: number; groups: GroupCard[]; page_size: number }>(`/api/groups${qs({ type, status, owner, page })}`),
    placeholderData: (prev) => prev,
  });
  const bulk = useQuery({
    queryKey: ["bulk-exact"],
    queryFn: () => api<BulkPreview>("/api/groups/bulk-exact"),
    enabled: bulkOpen,
  });
  const bulkMut = useMutation({
    mutationFn: () => post<{ groups: number; files: number; skipped: number }>("/api/groups/bulk-exact"),
    onSuccess: (r) => {
      toast(`Marked ${r.files} exact copies across ${r.groups} groups. Owners can now approve them under Approvals.`);
      setBulkOpen(false);
      qc.invalidateQueries();
    },
    onError: (e) => toast(String(e), true),
  });

  const proc = sys.data?.files.by_process_state ?? {};
  const total = (proc.done ?? 0) + (proc.pending ?? 0) + (proc.error ?? 0);
  const processing = (proc.pending ?? 0) > 0;
  const openTotal = summary.data ? TYPES.reduce((n, t) => n + (summary.data!.by_type[t]?.open ?? 0), 0) : 0;
  const reviewLink = `/review${qs({ type, owner, status })}`;

  return (
    <>
      <div className="page-head">
        <h1>Overview</h1>
        <div className="spacer" />
        <Link className="btn primary" to={reviewLink}>
          Start reviewing{openTotal ? ` (${openTotal} open)` : ""}
        </Link>
      </div>

      {processing && (
        <div className="card pad" style={{ marginBottom: 16 }}>
          <div className="row" style={{ marginBottom: 8 }}>
            <strong>Indexing the library…</strong>
            <span className="muted small">
              {(proc.done ?? 0).toLocaleString()} of {total.toLocaleString()} files processed
              {sys.data?.active_workers.length ? ` · workers: ${sys.data.active_workers.join(", ")}` : ""}
            </span>
          </div>
          <div className="progress">
            <div style={{ width: `${total ? ((proc.done ?? 0) / total) * 100 : 0}%` }} />
          </div>
          <div className="small faint" style={{ marginTop: 6 }}>
            Duplicate groups appear once processing finishes. You can start reviewing groups that are already found.
          </div>
        </div>
      )}

      <div className="stats">
        {TYPES.map((t) => {
          const c = summary.data?.by_type[t];
          return (
            <button
              key={t}
              className={cls("card stat", type === t && "on")}
              style={{ textAlign: "left", font: "inherit", color: "inherit" }}
              onClick={() => {
                setType(type === t ? "" : t);
                setPage(1);
              }}
              title={TYPE_HINT[t]}
            >
              <span className="lbl">{TYPE_LABEL[t]}</span>
              <span className="num">{(c?.open ?? 0).toLocaleString()}</span>
              <span className="small muted">
                open · {(c?.resolved ?? 0).toLocaleString()} resolved · {(c?.dismissed ?? 0).toLocaleString()} dismissed
              </span>
            </button>
          );
        })}
        <div className="card stat" style={{ cursor: "default" }}>
          <span className="lbl">Library</span>
          <span className="num">{((sys.data?.files.kinds.image ?? 0) + (sys.data?.files.kinds.video ?? 0)).toLocaleString()}</span>
          <span className="small muted">
            {(sys.data?.files.kinds.image ?? 0).toLocaleString()} photos · {(sys.data?.files.kinds.video ?? 0).toLocaleString()} videos ·{" "}
            {bytes(sys.data?.files.bytes)}
          </span>
        </div>
      </div>

      {(summary.data?.bulk_exact ?? 0) > 0 && (
        <div className="card pad row" style={{ marginBottom: 20, justifyContent: "space-between" }}>
          <div>
            <h3 style={{ margin: 0 }}>{summary.data!.bulk_exact.toLocaleString()} exact-copy groups haven't been looked at</h3>
            <div className="muted small">
              Byte-identical files are safe to clear in one go. The best copy (EXIF, resolution, original format) is kept; every
              other copy is <em>marked</em> and still needs its owner's approval before anything moves.
            </div>
          </div>
          <button className="btn primary" onClick={() => setBulkOpen(true)}>
            Review &amp; mark all…
          </button>
        </div>
      )}

      <div className="row" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>{type ? TYPE_LABEL[type] : "All groups"}</h2>
        <span className="muted">{groups.data ? `${groups.data.total.toLocaleString()} groups` : ""}</span>
        <div className="grow" />
        <div className="seg">
          {["open", "resolved", "dismissed", "all"].map((s) => (
            <button key={s} className={cls(status === s && "on")} onClick={() => { setStatus(s); setPage(1); }}>
              {s[0].toUpperCase() + s.slice(1)}
            </button>
          ))}
        </div>
        <select value={owner} onChange={(e) => { setOwner(e.target.value); setPage(1); }} aria-label="Owner">
          <option value="">Anyone's files</option>
          {facets.data?.owners.map((o) => (
            <option key={o.id} value={o.id}>Includes {o.name}'s files</option>
          ))}
          <option value="unassigned">Includes unassigned files</option>
        </select>
      </div>
      {type && <p className="muted small" style={{ marginTop: -4 }}>{TYPE_HINT[type]}</p>}

      {groups.isLoading ? (
        <Loading />
      ) : groups.data && groups.data.groups.length === 0 ? (
        <div className="card empty">
          <div className="big">✨</div>
          {status === "open" ? "No open duplicate groups here. Nice and tidy." : "Nothing to show."}
        </div>
      ) : (
        <div className="group-list">
          {groups.data?.groups.map((g) => (
            <div
              key={g.id}
              className="card group-row"
              onClick={() => navigate(`/review${qs({ type, owner, status, group: g.id })}`)}
              role="link"
            >
              <div className="strip">
                {g.members.map((m) => (
                  <Thumb
                    key={m.item_id}
                    file={m.file_id ? { id: m.file_id, thumb: true, kind: "image", duration: null, ext: "" } : null}
                    className={cls(m.suggested_keep && "keeper")}
                    dim={m.status === "quarantined" || m.status === "purged"}
                  />
                ))}
              </div>
              <div className="grow">
                <div className="row">
                  <strong>#{g.id}</strong>
                  <span className="badge accent">{TYPE_LABEL[g.type]}</span>
                  {g.type !== "exact" && <span className="badge">{Math.round(g.confidence * 100)}% match</span>}
                  {g.status !== "open" && <span className={cls("badge", g.status === "resolved" && "keep")}>{g.status}</span>}
                </div>
                <div className="small muted" style={{ marginTop: 4 }}>
                  {g.size} copies · owners: {[...new Set(g.members.map((m) => m.owner ?? "Unassigned"))].join(", ")}
                  {g.type !== "burst" && g.reclaim_bytes ? ` · frees ${bytes(g.reclaim_bytes)}` : ""}
                </div>
              </div>
              <span className="faint">›</span>
            </div>
          ))}
        </div>
      )}
      {groups.data && groups.data.total > groups.data.page_size && (
        <div className="row" style={{ justifyContent: "center", marginTop: 16 }}>
          <button className="btn sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ Prev</button>
          <span className="muted small">Page {page} of {Math.ceil(groups.data.total / groups.data.page_size)}</span>
          <button className="btn sm" disabled={page >= Math.ceil(groups.data.total / groups.data.page_size)} onClick={() => setPage(page + 1)}>Next ›</button>
        </div>
      )}

      {bulkOpen && (
        <Confirm
          title="Mark all exact copies?"
          confirmLabel={bulk.data ? `Mark ${bulk.data.files.toLocaleString()} copies` : "Mark copies"}
          busy={bulkMut.isPending || !bulk.data}
          onCancel={() => setBulkOpen(false)}
          onConfirm={() => bulkMut.mutate()}
        >
          {!bulk.data ? (
            <Loading />
          ) : (
            <div className="stack">
              <p style={{ margin: 0 }}>
                <strong>{bulk.data.groups.toLocaleString()}</strong> groups ·{" "}
                <strong>{bulk.data.files.toLocaleString()}</strong> duplicate files · frees{" "}
                <strong>{bytes(bulk.data.reclaim_bytes)}</strong>
              </p>
              <div className="row">
                {Object.entries(bulk.data.by_owner).map(([o, n]) => (
                  <span key={o} className="badge">{o}: {n.toLocaleString()} to approve</span>
                ))}
              </div>
              <div className="row" style={{ gap: 4 }}>
                {bulk.data.sample.slice(0, 12).map((s) => (
                  <Thumb key={s.group_id} file={{ id: s.keep_file_id, thumb: true, kind: "image", duration: null, ext: "" }} size={52} />
                ))}
              </div>
              <div className="banner info small">
                Nothing is moved yet. Each owner approves their own files under <strong>Approvals</strong>; approved files go to
                quarantine and can be restored.
              </div>
            </div>
          )}
        </Confirm>
      )}
    </>
  );
}
