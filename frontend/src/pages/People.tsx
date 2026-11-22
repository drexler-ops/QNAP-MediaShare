import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, faceThumbUrl, type PeopleList, type PersonSummary } from "../api";
import { Loading, Pager, pageCount, Spinner, usePageParam, useToast } from "../ui";
import { cls } from "../util";

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

const GROUPS = 60;

export default function People() {
  const qc = useQueryClient();
  const toast = useToast();
  const [showHidden, setShowHidden] = useState(false);
  const [page, setPage] = usePageParam();
  const q = useQuery({
    queryKey: ["people", showHidden, page],
    queryFn: () => api<PeopleList>(`/api/people?unnamed_page=${page}&unnamed_page_size=${GROUPS}${showHidden ? "&include_hidden=true" : ""}`),
    placeholderData: (prev) => prev,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      if (s?.regrouping) return 3000; // names are saved; groups and counts are catching up
      return s && s.enabled && s.photos_done < s.photos_total ? 8000 : false;
    },
  });
  const names = useQuery({ queryKey: ["people-names"], queryFn: () => api<{ id: number; name: string }[]>("/api/people/names") });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["people"] });
    qc.invalidateQueries({ queryKey: ["people-names"] });
  };
  const enable = useMutation({
    mutationFn: () => api("/api/admin/settings", { method: "PUT", json: { faces_enabled: true } }),
    onSuccess: () => { toast("Face recognition is on. Photos are being scanned in the background."); refresh(); },
    onError: (e) => toast(errMsg(e), true),
  });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) =>
      api<{ id: number; merged: boolean }>(`/api/people/${id}`, { method: "PATCH", json: body }),
    onSuccess: (r, v) => {
      if (v.body.name) toast(r.merged ? `Added to ${v.body.name} (same name, so the groups were merged).` : `Named ${v.body.name}.`);
      else if (v.body.hidden !== undefined) toast(v.body.hidden ? "Hidden. Show it again with \"Show hidden\"." : "Unhidden.");
      refresh();
    },
    onError: (e) => {
      toast(errMsg(e), true);
      refresh(); // the list may be out of date
    },
  });

  // Naming the last groups on the last page shrinks the list: step back to a page that exists.
  const lastPage = q.data ? pageCount(q.data.unnamed_total, GROUPS) : 1;
  useEffect(() => {
    if (q.data && page > lastPage) setPage(lastPage);
  }, [q.data, page, lastPage, setPage]);

  if (q.isLoading || !q.data) return <Loading />;
  const { status, named, unnamed, unnamed_total, review } = q.data;
  const processing = status.enabled && status.photos_done < status.photos_total;

  return (
    <>
      <div className="page-head">
        <h1>People</h1>
        <span className="muted">Faces found in everyone's photos. Name someone once and they're recognised from then on.</span>
        {status.regrouping && (
          <span className="updating" title="Names are saved. Matching faces to people in the background; counts update in a moment.">
            <Spinner /> Updating groups…
          </span>
        )}
        <div className="spacer" />
        {review > 0 && <Link to="/people/review" className="btn primary">Review {review} suggestion{review === 1 ? "" : "s"}</Link>}
        <label className="check small">
          <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} /> Show hidden
        </label>
      </div>

      {!status.enabled ? (
        <div className="card empty">
          <div className="big">🙂</div>
          <h2>Face recognition is off</h2>
          <p className="muted" style={{ maxWidth: 560, margin: "0 auto 14px" }}>
            Turning it on scans every photo on the NAS for faces and groups them by person. Everything stays on the NAS; nothing is sent
            anywhere. The first time, the NAS downloads InsightFace's face models (~290 MB; non-commercial license) and the first pass
            over the library can take a few hours.
          </p>
          <button className="btn primary lg" disabled={enable.isPending} onClick={() => enable.mutate()}>Turn on face recognition</button>
        </div>
      ) : (
        <>
          {processing && (
            <div className="card pad" style={{ marginBottom: 16 }}>
              <div className="row" style={{ marginBottom: 8 }}>
                <strong>{status.models_ready ? "Looking for faces…" : "Downloading face models…"}</strong>
                <span className="muted small">
                  {status.photos_done.toLocaleString()} of {status.photos_total.toLocaleString()} photos · {status.faces.toLocaleString()} faces so far
                </span>
              </div>
              <div className="progress"><div style={{ width: `${status.photos_total ? (status.photos_done / status.photos_total) * 100 : 0}%` }} /></div>
            </div>
          )}

          <h2>Named</h2>
          {named.length === 0 ? (
            <p className="muted">Nobody named yet. Name one of the groups below.</p>
          ) : (
            <div className="people-grid">
              {named.map((p) => (
                <Link key={p.id} to={`/people/${p.id}`} className={cls("person-card", p.hidden && "hidden-person")}>
                  <Avatar person={p} />
                  <strong>{p.name}</strong>
                  <span className="small muted">{p.photos.toLocaleString()} photo{p.photos === 1 ? "" : "s"}</span>
                </Link>
              ))}
            </div>
          )}

          <h2 style={{ marginTop: 28 }}>
            Who's this? <span className="muted small" style={{ fontWeight: 400 }}>{unnamed_total.toLocaleString()} unnamed group{unnamed_total === 1 ? "" : "s"}</span>
          </h2>
          <Pager top page={page} pages={lastPage} onPage={setPage} />
          {unnamed.length === 0 ? (
            <p className="muted">{processing ? "Groups appear as photos are scanned." : "No unnamed groups."}</p>
          ) : (
            <div className="people-grid">
              {unnamed.map((p) => (
                <UnnamedCard
                  key={p.id}
                  person={p}
                  names={names.data ?? []}
                  busy={patch.isPending && patch.variables?.id === p.id}
                  onName={(name) => patch.mutate({ id: p.id, body: { name } })}
                  onHide={() => patch.mutate({ id: p.id, body: { hidden: !p.hidden } })}
                />
              ))}
            </div>
          )}
          <Pager page={page} pages={lastPage} onPage={setPage} />
          <datalist id="people-names">{names.data?.map((n) => <option key={n.id} value={n.name} />)}</datalist>
        </>
      )}
    </>
  );
}

export function Avatar({ person, size = 112 }: { person: Pick<PersonSummary, "cover_face_id" | "name">; size?: number }) {
  return (
    <span className="avatar" style={{ width: size, height: size }}>
      {person.cover_face_id ? <img src={faceThumbUrl(person.cover_face_id)} alt="" loading="lazy" /> : <span>{(person.name ?? "?")[0]}</span>}
    </span>
  );
}

function UnnamedCard({
  person,
  busy,
  onName,
  onHide,
}: {
  person: PersonSummary;
  busy: boolean;
  names: { id: number; name: string }[];
  onName: (name: string) => void;
  onHide: () => void;
}) {
  const [name, setName] = useState("");
  return (
    <div className={cls("person-card", person.hidden && "hidden-person")}>
      <Link to={`/people/${person.id}`} title="See all photos in this group">
        <Avatar person={person} />
      </Link>
      <span className="small muted">{person.photos.toLocaleString()} photos</span>
      <form className="row" style={{ flexWrap: "nowrap", gap: 4 }} onSubmit={(e) => { e.preventDefault(); if (name.trim()) onName(name.trim()); }}>
        <input type="text" list="people-names" placeholder="Name…" value={name} onChange={(e) => setName(e.target.value)} style={{ width: "100%" }} aria-label="Name this person" disabled={busy} />
        <button className="btn sm primary" disabled={!name.trim() || busy} aria-label="Save name">{busy ? <Spinner /> : "✓"}</button>
      </form>
      <button className="btn sm ghost" onClick={onHide} disabled={busy}>{person.hidden ? "Unhide" : "Hide"}</button>
    </div>
  );
}
