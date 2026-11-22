import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, qs, type Facets, type ItemInfo } from "../api";
import { useViewer } from "../components/MediaViewer";
import AlbumPicker from "../components/AlbumPicker";
import { Loading, Thumb, useTileSelection } from "../ui";
import { date } from "../util";

export interface BrowseFilters {
  owner: string;
  kind: string;
  has_exif: string;
  has_gps: string;
  camera: string;
  date_from: string;
  date_to: string;
  q: string;
  status: string;
  sort: string;
  live: string;
  people: string;
}

const EMPTY: BrowseFilters = {
  owner: "", kind: "", has_exif: "", has_gps: "", camera: "", date_from: "", date_to: "", q: "", status: "", sort: "taken_desc", live: "", people: "",
};

export function FilterBar({
  value,
  onChange,
  facets,
  compact,
}: {
  value: BrowseFilters;
  onChange: (v: BrowseFilters) => void;
  facets?: Facets;
  compact?: boolean;
}) {
  const set = (k: keyof BrowseFilters) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    onChange({ ...value, [k]: e.target.value });
  const peopleQ = useQuery({ queryKey: ["people-names"], queryFn: () => api<{ id: number; name: string }[]>("/api/people/names") });
  const chosen = (value.people || "").split(",").filter(Boolean).map(Number);
  const setPeople = (ids: number[]) => onChange({ ...value, people: ids.join(",") });
  const [open, setOpen] = useState(false);
  const active = (Object.keys(value) as (keyof BrowseFilters)[]).filter((k) => k !== "sort" && k !== "q" && value[k]).length;
  return (
    <div className="row filter-bar" style={{ marginBottom: 16 }}>
      {!compact && (
        <input type="search" placeholder="Search file names / folders" value={value.q} onChange={set("q")} className="filter-search" />
      )}
      <button className="btn filter-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        Filters{active ? ` (${active})` : ""} {open ? "▴" : "▾"}
      </button>
      <div className={"row filter-body" + (open ? " open" : "")}>
      <select value={value.owner} onChange={set("owner")} aria-label="Owner">
        <option value="">Everyone</option>
        {facets?.owners.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
        <option value="unassigned">Unassigned</option>
      </select>
      {(peopleQ.data?.length ?? 0) > 0 && (
        <span className="row" style={{ gap: 4 }}>
          {chosen.map((pid) => (
            <button key={pid} className="badge accent person-chip" onClick={() => setPeople(chosen.filter((x) => x !== pid))} title="Remove">
              {peopleQ.data?.find((p) => p.id === pid)?.name ?? "?"} ✕
            </button>
          ))}
          <select value="" onChange={(e) => e.target.value && setPeople([...chosen, Number(e.target.value)])} aria-label="With person">
            <option value="">{chosen.length ? "＋ and…" : "With person…"}</option>
            {peopleQ.data?.filter((p) => !chosen.includes(p.id)).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </span>
      )}
      <select value={value.kind} onChange={set("kind")} aria-label="Type">
        <option value="">Photos &amp; videos</option>
        <option value="image">Photos</option>
        <option value="video">Videos</option>
      </select>
      <select value={value.camera} onChange={set("camera")} aria-label="Camera" style={{ maxWidth: 200 }}>
        <option value="">Any camera</option>
        {facets?.cameras.map((c) => <option key={c.model} value={c.model}>{c.model} ({c.count})</option>)}
      </select>
      <input type="date" value={value.date_from} onChange={set("date_from")} aria-label="From date" title="Taken from" />
      <input type="date" value={value.date_to} onChange={set("date_to")} aria-label="To date" title="Taken until" />
      {!compact && (
        <>
          <select value={value.has_exif} onChange={set("has_exif")} aria-label="EXIF">
            <option value="">EXIF: any</option>
            <option value="true">Has EXIF</option>
            <option value="false">No EXIF</option>
          </select>
          <select value={value.has_gps} onChange={set("has_gps")} aria-label="GPS">
            <option value="">GPS: any</option>
            <option value="true">Has GPS</option>
            <option value="false">No GPS</option>
          </select>
          <select value={value.live} onChange={set("live")} aria-label="Live Photos">
            <option value="">All</option>
            <option value="true">Live Photos</option>
          </select>
          <select value={value.status} onChange={set("status")} aria-label="Status">
            <option value="">Current files</option>
            <option value="pending_delete">Awaiting approval</option>
            <option value="quarantined">In quarantine</option>
            <option value="missing">Missing on disk</option>
            <option value="all">Everything</option>
          </select>
          <select value={value.sort} onChange={set("sort")} aria-label="Sort">
            <option value="taken_desc">Newest taken</option>
            <option value="taken_asc">Oldest taken</option>
            <option value="added_desc">Recently added</option>
            <option value="size_desc">Largest</option>
            <option value="name">Path</option>
          </select>
        </>
      )}
      <button className="btn sm ghost" onClick={() => onChange({ ...EMPTY })}>Clear</button>
      </div>
    </div>
  );
}

export default function Browse() {
  const [filters, setFilters] = useState<BrowseFilters>(EMPTY);
  const [page, setPage] = useState(1);
  const viewer = useViewer();
  const [picking, setPicking] = useState(false);
  const facets = useQuery({ queryKey: ["facets"], queryFn: () => api<Facets>("/api/facets") });
  const items = useQuery({
    queryKey: ["items", filters, page],
    queryFn: () => api<{ total: number; page_size: number; items: ItemInfo[] }>(`/api/items${qs({ ...filters, page, page_size: 60 })}`),
    placeholderData: (prev) => prev,
  });
  const pages = items.data ? Math.max(1, Math.ceil(items.data.total / items.data.page_size)) : 1;
  const list = items.data?.items ?? [];

  // Selection survives paging, so you can collect photos across several pages.
  const sel = useTileSelection(list.map((x) => x.id));
  const { selecting, selected, setSelected, stop: stopSelecting } = sel;
  const clickTile = (idx: number, e: React.MouseEvent) => {
    if (sel.click(idx, e)) return;
    viewer.open(list.map((x) => ({ item: x.id, file: x.primary.id })), idx);
  };

  return (
    <>
      <div className="page-head">
        <h1>Browse</h1>
        <span className="muted">{items.data ? `${items.data.total.toLocaleString()} items` : ""}</span>
        <div className="spacer" />
        <Link to="/upload" className="btn">⬆ Upload</Link>
        <button className={selecting ? "btn primary" : "btn"} onClick={() => (selecting ? stopSelecting() : sel.start())}>
          {selecting ? "Done" : "Select"}
        </button>
      </div>
      <FilterBar value={filters} onChange={(v) => { setFilters(v); setPage(1); }} facets={facets.data} />
      {selecting && (
        <div className="card pad row select-bar">
          <strong>{selected.size} selected</strong>
          <span className="small faint hide-touch">Click to select · Shift-click or drag for a range</span>
          <span className="small faint show-touch-inline">Tap to select · long-press and drag for a range</span>
          <button className="btn sm" onClick={() => setSelected((cur) => new Set([...cur, ...list.map((x) => x.id)]))}>Select page</button>
          <button className="btn sm" disabled={!selected.size} onClick={() => setSelected(new Set())}>Clear</button>
          <div className="grow" />
          <button className="btn sm primary" disabled={!selected.size} onClick={() => setPicking(true)}>📚 Add to album</button>
        </div>
      )}
      {items.isLoading ? (
        <Loading />
      ) : list.length === 0 ? (
        <div className="card empty">No files match these filters.</div>
      ) : (
        <div className="grid">
          {list.map((it, idx) => (
            <div
              key={it.id}
              className={selecting && selected.has(it.id) ? "tile selected" : "tile"}
              {...sel.tileProps(idx)}
              onClick={(e) => clickTile(idx, e)}
            >
              <div style={{ position: "relative" }}>
                <Thumb file={it.primary} size="100%" live={it.is_live_photo} dim={it.primary.status !== "active"} />
                <div style={{ position: "absolute", top: 6, right: 6, display: "flex", gap: 4 }}>
                  {it.in_group && <span className="badge accent" title="Part of a duplicate group">DUP</span>}
                  {!it.primary.has_exif && <span className="badge warn" title="No EXIF metadata">no EXIF</span>}
                </div>
                {selecting && <span className="tile-check">{selected.has(it.id) ? "✓" : ""}</span>}
              </div>
              <div className="tile-meta" title={it.primary.path}>
                {it.owner} · {date(it.primary.taken_at, false)}
              </div>
            </div>
          ))}
        </div>
      )}
      {pages > 1 && (
        <div className="row" style={{ justifyContent: "center", marginTop: 20 }}>
          <button className="btn sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ Prev</button>
          <span className="muted small">Page {page} of {pages.toLocaleString()}</span>
          <button className="btn sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next ›</button>
        </div>
      )}
      {picking && <AlbumPicker itemIds={[...selected]} onClose={() => setPicking(false)} onDone={stopSelecting} />}
    </>
  );
}
