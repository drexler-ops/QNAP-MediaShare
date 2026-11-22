import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api, post, qs, type Facets, type GroupDetail, type GroupMember, type GroupType, type Me } from "../api";
import { StillView, VideoPlayer } from "../components/MediaView";
import { useViewer } from "../components/MediaViewer";
import { Loading, Modal, useToast } from "../ui";
import { bytes, camera, cls, date, dims, duration, megapixels, STATUS_LABEL, TYPE_HINT, TYPE_LABEL } from "../util";

type Action = "keep" | "delete";
const LOCKED_STATES = ["approved", "quarantined", "purged"];

function lockReason(m: GroupMember): string | null {
  if (m.decision && LOCKED_STATES.includes(m.decision.state)) return STATUS_LABEL[m.primary.status] ?? m.decision.state;
  if (m.primary.status === "quarantined" || m.primary.status === "purged" || m.primary.status === "missing")
    return STATUS_LABEL[m.primary.status];
  if (m.deleting_elsewhere) return "Being deleted via another group";
  return null;
}

function initialAction(m: GroupMember, type: GroupType): Action {
  if (lockReason(m)) return m.primary.status === "active" && !m.deleting_elsewhere ? "keep" : "delete";
  if (m.decision?.action === "keep" || m.decision?.action === "delete") return m.decision.action;
  if (type === "burst") return "keep"; // similar shots: nothing deleted unless you choose
  return m.suggested_keep ? "keep" : "delete";
}

interface Row {
  label: string;
  value: (m: GroupMember) => string;
  score?: (m: GroupMember) => number;
  videoOnly?: boolean;
}

const ROWS: Row[] = [
  { label: "Owner", value: (m) => m.owner },
  { label: "Folder", value: (m) => m.primary.folder || "/" },
  { label: "File name", value: (m) => m.primary.name },
  { label: "Format", value: (m) => m.primary.ext.toUpperCase() + (m.primary.codec ? ` · ${m.primary.codec}` : "") },
  { label: "Dimensions", value: (m) => `${dims(m.primary)} ${megapixels(m.primary)}`, score: (m) => (m.primary.width ?? 0) * (m.primary.height ?? 0) },
  { label: "File size", value: (m) => bytes(m.primary.size), score: (m) => m.primary.size },
  { label: "Duration", value: (m) => duration(m.primary.duration), videoOnly: true },
  { label: "Date taken", value: (m) => date(m.primary.taken_at), score: (m) => (m.primary.taken_at ? 1 : 0) },
  { label: "Camera", value: (m) => camera(m.primary), score: (m) => (m.primary.make || m.primary.model ? 1 : 0) },
  {
    label: "GPS location",
    value: (m) => (m.primary.has_gps ? `${m.primary.gps_lat?.toFixed(4)}, ${m.primary.gps_lon?.toFixed(4)}` : "None"),
    score: (m) => (m.primary.has_gps ? 1 : 0),
  },
  { label: "EXIF richness", value: (m) => (m.primary.has_exif ? `${m.primary.exif_score}/9` : "No EXIF"), score: (m) => m.primary.exif_score },
  { label: "Live Photo", value: (m) => (m.is_live_photo ? "Yes (photo + motion)" : "No"), score: (m) => (m.is_live_photo ? 1 : 0) },
  { label: "Added to NAS", value: (m) => date(m.primary.first_seen_at, false) },
  { label: "Match", value: (m) => (m.match_type === "exact" ? "Identical bytes" : m.match_type === "burst" ? "Similar shot" : "Visually identical") },
  { label: "Keeper score", value: (m) => m.score.toFixed(1), score: (m) => m.score },
];

function warnings(members: GroupMember[], draft: Record<number, Action>): string[] {
  const kept = members.filter((m) => draft[m.id] === "keep");
  const dropped = members.filter((m) => draft[m.id] === "delete" && !lockReason(m));
  if (!dropped.length) return [];
  const out: string[] = [];
  const any = (xs: GroupMember[], f: (m: GroupMember) => boolean) => xs.some(f);
  if (any(dropped, (m) => m.primary.has_gps) && !any(kept, (m) => m.primary.has_gps))
    out.push("You'd delete the only copy with a GPS location.");
  if (any(dropped, (m) => m.primary.has_exif) && !any(kept, (m) => m.primary.has_exif))
    out.push("You'd delete the only copy with camera metadata (EXIF).");
  if (any(dropped, (m) => !!m.primary.taken_at) && !any(kept, (m) => !!m.primary.taken_at))
    out.push("You'd delete the only copy that knows when it was taken.");
  if (any(dropped, (m) => m.is_live_photo) && !any(kept, (m) => m.is_live_photo))
    out.push("You'd delete the only Live Photo version (the kept copy has no motion).");
  const px = (m: GroupMember) => (m.primary.width ?? 0) * (m.primary.height ?? 0);
  if (Math.max(0, ...dropped.map(px)) > Math.max(0, ...kept.map(px)) * 1.05)
    out.push("A copy you're deleting has a higher resolution than every copy you're keeping.");
  return out;
}

export default function Review({ me }: { me: Me }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const type = params.get("type") ?? "";
  const owner = params.get("owner") ?? "";
  const status = params.get("status") ?? "open";
  const groupParam = Number(params.get("group")) || null;

  const facets = useQuery({ queryKey: ["facets"], queryFn: () => api<Facets>("/api/facets") });
  const queue = useQuery({
    queryKey: ["queue", type, owner, status],
    queryFn: () => api<{ ids: number[] }>(`/api/groups/queue${qs({ type, owner, status })}`),
    staleTime: Infinity,
  });
  const ids = useMemo(() => {
    const base = queue.data?.ids ?? [];
    return groupParam && !base.includes(groupParam) ? [groupParam, ...base] : base;
  }, [queue.data, groupParam]);
  const [finished, setFinished] = useState(false);
  const current = groupParam ?? ids[0] ?? null;
  const index = current ? ids.indexOf(current) : -1;

  const group = useQuery({
    queryKey: ["group", current],
    queryFn: () => api<GroupDetail>(`/api/groups/${current}`),
    enabled: !!current,
  });

  const [draft, setDraft] = useState<Record<number, Action>>({});
  const [focus, setFocus] = useState(0);
  // Phones: the copies are a swipeable carousel; the card in view is the selected one.
  const cardsRef = useRef<HTMLDivElement>(null);
  const narrow = () => window.matchMedia("(max-width: 700px)").matches;
  const onCardsScroll = () => {
    const el = cardsRef.current;
    if (!el || !narrow()) return;
    const card = el.querySelector<HTMLElement>(".mcard");
    if (!card) return;
    const step = card.offsetWidth + 12;
    const i = Math.round(el.scrollLeft / step);
    setFocus((f) => (f === i ? f : i));
  };
  const showCard = (i: number) => {
    setFocus(i);
    const el = cardsRef.current;
    const card = el?.querySelectorAll<HTMLElement>(".mcard")[i];
    if (el && card && narrow()) el.scrollTo({ left: card.offsetLeft - el.offsetLeft, behavior: "smooth" });
  };
  const [zoom, setZoom] = useState(1);
  const [point, setPoint] = useState<{ x: number; y: number } | null>(null);
  const [playing, setPlaying] = useState<number | null>(null);
  const [help, setHelp] = useState(false);
  const viewer = useViewer();

  const g = group.data;
  const openViewer = useCallback(
    (i: number) => {
      if (g) viewer.open(g.members.map((m) => ({ item: m.id, file: m.primary.id })), i);
    },
    [g, viewer],
  );
  useEffect(() => {
    if (!g) return;
    setDraft(Object.fromEntries(g.members.map((m) => [m.id, initialAction(m, g.type)])));
    setFocus(0);
    setPlaying(null);
  }, [g]);

  const setFilter = (k: string, v: string) =>
    setParams((p) => {
      if (v) p.set(k, v);
      else p.delete(k);
      p.delete("group");
      return p;
    });

  const go = useCallback(
    (id: number | undefined) => {
      if (!id) {
        setFinished(true);
        return;
      }
      setFinished(false);
      setParams((p) => {
        p.set("group", String(id));
        return p;
      }, { replace: true });
    },
    [setParams],
  );
  const next = useCallback(() => go(ids[index + 1]), [go, ids, index]);
  const prev = useCallback(() => index > 0 && go(ids[index - 1]), [go, ids, index]);

  const editable = g ? g.members.filter((m) => !lockReason(m)) : [];
  const keptCount = g ? g.members.filter((m) => draft[m.id] === "keep" && (!lockReason(m) || m.primary.status === "active")).length : 0;
  const warn = g ? warnings(g.members, draft) : [];

  const save = useMutation({
    mutationFn: () =>
      post<GroupDetail>(`/api/groups/${current}/decisions`, {
        decisions: editable.map((m) => ({ item_id: m.id, action: draft[m.id] })),
      }),
    onSuccess: (res) => {
      const deleting = res.members.filter((m) => m.decision?.action === "delete" && m.decision.state === "marked");
      const others = [...new Set(deleting.filter((m) => m.owner_id !== null && m.owner_id !== me.id).map((m) => m.owner))];
      const mine = deleting.filter((m) => m.owner_id === null || m.owner_id === me.id).length;
      if (deleting.length) {
        const parts = [];
        if (mine) parts.push(`${mine} need your approval`);
        if (others.length) parts.push(`waiting on ${others.join(", ")}`);
        toast(`Marked ${deleting.length} for deletion — ${parts.join("; ")}.`);
      } else toast("Saved — keeping everything in this group.");
      qc.setQueryData(["group", current], res);
      qc.invalidateQueries({ queryKey: ["summary"] });
      qc.invalidateQueries({ queryKey: ["pending-count"] });
      qc.invalidateQueries({ queryKey: ["groups"] });
      next();
    },
    onError: (e) => toast(String(e instanceof Error ? e.message : e), true),
  });

  const dismiss = useMutation({
    mutationFn: () => post(`/api/groups/${current}/${g?.status === "dismissed" ? "reopen" : "dismiss"}`),
    onSuccess: () => {
      toast(g?.status === "dismissed" ? "Group reopened." : "Marked as not duplicates.");
      qc.invalidateQueries({ queryKey: ["group", current] });
      qc.invalidateQueries({ queryKey: ["summary"] });
      if (g?.status !== "dismissed") next();
    },
    onError: (e) => toast(String(e instanceof Error ? e.message : e), true),
  });

  const setAction = useCallback(
    (m: GroupMember | undefined, a: Action | "toggle") => {
      if (!m || lockReason(m)) return;
      setDraft((d) => ({ ...d, [m.id]: a === "toggle" ? (d[m.id] === "keep" ? "delete" : "keep") : a }));
    },
    [],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (["INPUT", "SELECT", "TEXTAREA"].includes(t.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (!g || viewer.isOpen) return;
      const m = g.members[focus];
      const k = e.key.toLowerCase();
      if (k === "arrowright") next();
      else if (k === "arrowleft") prev();
      else if (/^[1-9]$/.test(k) && Number(k) <= g.members.length) setFocus(Number(k) - 1);
      else if (k === "tab") {
        e.preventDefault();
        setFocus((f) => (f + (e.shiftKey ? -1 + g.members.length : 1)) % g.members.length);
      } else if (k === "k") setAction(m, "keep");
      else if (k === "d") setAction(m, "delete");
      else if (k === " ") {
        e.preventDefault();
        setAction(m, "toggle");
      } else if (k === "enter") {
        if (keptCount > 0 && !save.isPending && editable.length) save.mutate();
        else if (!editable.length) next();
      } else if (k === "s") next();
      else if (k === "z") setZoom((z) => (z > 1 ? 1 : 2.5));
      else if (k === "n") dismiss.mutate();
      else if (k === "?") setHelp((h) => !h);
      else if (k === "v") openViewer(focus);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [g, focus, next, prev, setAction, keptCount, save, dismiss, editable.length, viewer.isOpen, openViewer]);

  const filters = (
    <div className="row">
      <select value={type} onChange={(e) => setFilter("type", e.target.value)} aria-label="Group type">
        <option value="">All types</option>
        {(["exact", "reencode", "video", "burst"] as GroupType[]).map((t) => (
          <option key={t} value={t}>{TYPE_LABEL[t]}</option>
        ))}
      </select>
      <select value={owner} onChange={(e) => setFilter("owner", e.target.value)} aria-label="Owner">
        <option value="">Anyone's files</option>
        {facets.data?.owners.map((o) => (
          <option key={o.id} value={o.id}>Includes {o.name}'s</option>
        ))}
        <option value="unassigned">Includes unassigned</option>
      </select>
      <select value={status} onChange={(e) => setFilter("status", e.target.value)} aria-label="Status">
        <option value="open">Open</option>
        <option value="resolved">Resolved</option>
        <option value="dismissed">Dismissed</option>
        <option value="all">All</option>
      </select>
      <button className="btn sm ghost" onClick={() => qc.invalidateQueries({ queryKey: ["queue"] })} title="Reload the list of groups">
        ↻ Refresh
      </button>
    </div>
  );

  if (queue.isLoading) return <Loading what="Loading review queue" />;
  if (!current || finished) {
    return (
      <>
        <div className="page-head">
          <h1>Review</h1>
          <div className="spacer" />
          {filters}
        </div>
        <div className="card empty">
          <div className="big">🎉</div>
          <h2>{finished ? "You reached the end of this queue." : "Nothing to review with these filters."}</h2>
          <p>
            Marked files now wait for their owners under <Link to="/pending">Approvals</Link>.
          </p>
          {finished && (
            <button className="btn" onClick={() => { setFinished(false); qc.invalidateQueries({ queryKey: ["queue"] }); setParams((p) => { p.delete("group"); return p; }); }}>
              Reload queue
            </button>
          )}
        </div>
      </>
    );
  }

  return (
    <>
      <div className="review-head">
        <h1>Review</h1>
        <span className="muted">
          Group {index + 1} of {ids.length.toLocaleString()}
        </span>
        {g && (
          <>
            <span className="badge accent">{TYPE_LABEL[g.type]}</span>
            {g.type !== "exact" && <span className="badge">{Math.round(g.confidence * 100)}% match</span>}
            {g.status !== "open" && <span className={cls("badge", g.status === "resolved" ? "keep" : "")}>{g.status}</span>}
            <span className="faint small">#{g.id}</span>
          </>
        )}
        <div className="grow" />
        {filters}
      </div>

      {group.isLoading || !g ? (
        <Loading />
      ) : (
        <>
          <p className="muted small" style={{ marginTop: -6 }}>{TYPE_HINT[g.type]}</p>
          {warn.length > 0 && (
            <div className="stack" style={{ marginBottom: 12, gap: 6 }}>
              {warn.map((w) => (
                <div key={w} className="banner">⚠ {w}</div>
              ))}
            </div>
          )}
          <div className="review-cards" ref={cardsRef} onScroll={onCardsScroll}>
            {g.members.map((m, i) => {
              const a = draft[m.id];
              const locked = lockReason(m);
              const f = m.primary;
              return (
                <div
                  key={m.id}
                  className={cls("mcard", a, i === focus && "focus")}
                  onClick={() => setFocus(i)}
                >
                  <div
                    className="viewer"
                    onMouseLeave={() => setPoint(null)}
                    onDoubleClick={(e) => { e.stopPropagation(); openViewer(i); }}
                    title="Double-click to view full screen"
                  >
                    <div className="ribbon">
                      <span className={cls("badge", a)}>{a === "keep" ? "KEEP" : "DELETE"}</span>
                      {m.suggested_keep && <span className="badge keep">★ Suggested keeper</span>}
                      {m.is_live_photo && <span className="badge">LIVE</span>}
                    </div>
                    <span className="idx">{i + 1}</span>
                    {f.kind === "video" ? (
                      playing === m.id ? (
                        <VideoPlayer file={f} autoPlay />
                      ) : (
                        <button
                          className="btn ghost"
                          style={{ width: "100%", height: "100%", padding: 0, borderRadius: 0 }}
                          onClick={(e) => { e.stopPropagation(); setPlaying(m.id); }}
                          aria-label="Play video"
                        >
                          <StillView file={f} />
                          <span className="badge" style={{ position: "absolute", fontSize: 14, padding: "6px 14px" }}>▶ Play {duration(f.duration)}</span>
                        </button>
                      )
                    ) : (
                      <StillView file={f} zoom={zoom} focus={point} onMove={setPoint} />
                    )}
                  </div>
                  <div className="info">
                    <div className="name" title={f.path}>{f.name}</div>
                    <div className="small muted mono" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={f.folder}>
                      {f.folder || "/"}
                    </div>
                    <div className="row small" style={{ marginTop: 4 }}>
                      <span className="badge">{m.owner}</span>
                      <span className="badge">{f.ext.toUpperCase()}</span>
                      <span className="badge">{dims(f)}</span>
                      <span className="badge">{bytes(f.size)}</span>
                      {!f.has_exif && <span className="badge warn">No EXIF</span>}
                      {f.has_gps && <span className="badge keep">GPS</span>}
                      {m.match_type === "exact" && g.type !== "exact" && <span className="badge accent">identical copy</span>}
                      {m.albums > 0 && (
                        <span className="badge" title="If this copy is deleted, its albums switch to the copy you keep">
                          📚 in {m.albums} {m.albums === 1 ? "album" : "albums"}
                        </span>
                      )}
                    </div>
                    {m.score_breakdown?.notes?.length ? (
                      <div className="notes small muted" style={{ marginTop: 2 }}>
                        {m.score_breakdown.notes.join(" · ")}
                      </div>
                    ) : null}
                    {m.decision && m.decision.state === "marked" && (
                      <div className="small" style={{ color: "var(--warn)", marginTop: 2 }}>
                        Marked for deletion by {m.decision.marked_by} · awaiting {m.owner === "Unassigned" ? "anyone's" : `${m.owner}'s`} approval
                      </div>
                    )}
                    {locked && <div className="small" style={{ color: "var(--text-3)", marginTop: 2 }}>🔒 {locked}</div>}
                  </div>
                  <div className="actions">
                    <button
                      className={cls("btn sm toggle-keep", a === "keep" && "on")}
                      disabled={!!locked}
                      onClick={(e) => { e.stopPropagation(); setFocus(i); setAction(m, "keep"); }}
                    >
                      Keep <kbd>K</kbd>
                    </button>
                    <button
                      className={cls("btn sm toggle-delete", a === "delete" && "on")}
                      disabled={!!locked}
                      onClick={(e) => { e.stopPropagation(); setFocus(i); setAction(m, "delete"); }}
                    >
                      Delete <kbd>D</kbd>
                    </button>
                    {f.status !== "purged" && (
                      <button className="btn sm ghost" onClick={(e) => { e.stopPropagation(); setFocus(i); openViewer(i); }} title="View full screen (V)">
                        ⤢ View
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {g.members.length > 1 && (
            <div className="card-dots" role="tablist" aria-label="Copies">
              {g.members.map((m, i) => (
                <button key={m.id} className={cls("card-dot", i === focus && "on", draft[m.id])} onClick={() => showCard(i)} aria-label={`Copy ${i + 1}`} />
              ))}
            </div>
          )}
          <div className="card diff">
            <table>
              <tbody>
                {ROWS.filter((r) => !r.videoOnly || g.members.some((m) => m.primary.kind === "video")).map((r) => {
                  const values = g.members.map(r.value);
                  const differs = new Set(values).size > 1;
                  const scores = r.score ? g.members.map(r.score) : null;
                  const hi = scores ? Math.max(...scores) : 0;
                  const lo = scores ? Math.min(...scores) : 0;
                  return (
                    <tr key={r.label} className={cls(differs && "differs")}>
                      <th>{r.label}</th>
                      {g.members.map((m, i) => (
                        <td
                          key={m.id}
                          className={cls(differs && scores && hi !== lo && scores[i] === hi && "best", differs && scores && hi !== lo && scores[i] === lo && "worst")}
                        >
                          {values[i]}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="review-foot">
            <button className="btn" onClick={prev} disabled={index <= 0} title="Previous group (←)">‹ Prev</button>
            <button className="btn" onClick={next} title="Skip without saving (S)">Skip <kbd>S</kbd></button>
            <button className="btn ghost" onClick={() => dismiss.mutate()} title="These aren't duplicates (N)">
              {g.status === "dismissed" ? "Reopen group" : <>Not duplicates <kbd>N</kbd></>}
            </button>
            <div className="grow" />
            <div className="keys">
              <span><kbd>1</kbd>–<kbd>9</kbd> select</span>
              <span><kbd>Space</kbd> toggle</span>
              <span><kbd>Z</kbd> zoom {zoom > 1 ? "on" : "off"}</span>
              <span><kbd>V</kbd> view</span>
              <span><kbd>←</kbd><kbd>→</kbd> groups</span>
              <button className="btn ghost sm" onClick={() => setHelp(true)}><kbd>?</kbd> help</button>
            </div>
            {editable.length === 0 ? (
              <button className="btn primary" onClick={next}>Next ›</button>
            ) : (
              <button
                className="btn primary lg"
                disabled={keptCount === 0 || save.isPending}
                onClick={() => save.mutate()}
                title={keptCount === 0 ? "Keep at least one copy" : "Save decisions and go to the next group (Enter)"}
              >
                {keptCount === 0
                  ? "Keep at least one copy"
                  : `Save & next — delete ${editable.filter((m) => draft[m.id] === "delete").length}`}{" "}
                <kbd>Enter</kbd>
              </button>
            )}
          </div>
        </>
      )}

      {help && (
        <Modal title="Keyboard shortcuts" onClose={() => setHelp(false)}>
          <dl className="kv">
            <dt><kbd>1</kbd>–<kbd>9</kbd>, <kbd>Tab</kbd></dt><dd>Select a copy</dd>
            <dt><kbd>K</kbd> / <kbd>D</kbd></dt><dd>Keep / delete the selected copy</dd>
            <dt><kbd>Space</kbd></dt><dd>Toggle keep/delete</dd>
            <dt><kbd>Enter</kbd></dt><dd>Save decisions and go to the next group</dd>
            <dt><kbd>S</kbd></dt><dd>Skip this group for now</dd>
            <dt><kbd>N</kbd></dt><dd>Not duplicates (dismiss the group)</dd>
            <dt><kbd>←</kbd> / <kbd>→</kbd></dt><dd>Previous / next group</dd>
            <dt><kbd>Z</kbd></dt><dd>Zoom loupe — hover any photo; all copies zoom to the same spot</dd>
            <dt><kbd>V</kbd> / double-click</dt><dd>View the selected copy full screen (full resolution, videos play); ← → steps through the group</dd>
          </dl>
          <p className="muted small">
            Saving only <em>marks</em> files. Each owner approves deletion of their own files under Approvals; approved files move
            to quarantine and can be restored until they are purged.
          </p>
        </Modal>
      )}
    </>
  );
}
