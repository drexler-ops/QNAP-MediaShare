import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import L from "leaflet";
import "leaflet.markercluster";
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";
import { api, qs, type Facets } from "../api";
import ItemModal from "../components/ItemModal";
import { thumbUrl } from "../util";
import { useAppConfig } from "../ui";
import { FilterBar, type BrowseFilters } from "./Browse";

export default function MapPage() {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.MarkerClusterGroup | null>(null);
  const [view, setView] = useState<number | null>(null);
  const cfg = useAppConfig();
  const [filters, setFilters] = useState<BrowseFilters>({
    owner: "", kind: "", has_exif: "", has_gps: "", camera: "", date_from: "", date_to: "", q: "", status: "", sort: "", live: "",
  });
  const facets = useQuery({ queryKey: ["facets"], queryFn: () => api<Facets>("/api/facets") });
  const points = useQuery({
    queryKey: ["map", filters.owner, filters.kind, filters.camera, filters.date_from, filters.date_to],
    queryFn: () =>
      api<{ points: [number, number, number, number][] }>(
        `/api/map/points${qs({ owner: filters.owner, kind: filters.kind, camera: filters.camera, date_from: filters.date_from, date_to: filters.date_to })}`,
      ),
  });

  useEffect(() => {
    if (!el.current || map.current || !cfg.map_tile_url) return;
    const m = L.map(el.current, { worldCopyJump: true }).setView([30, 0], 2);
    L.tileLayer(cfg.map_tile_url, { maxZoom: 19, attribution: cfg.map_attribution }).addTo(m);
    m.on("popupopen", (e) => {
      e.popup.getElement()?.querySelectorAll<HTMLElement>("[data-item]").forEach((node) =>
        node.addEventListener("click", () => setView(Number(node.dataset.item))),
      );
    });
    map.current = m;
    return () => {
      m.remove();
      map.current = null;
    };
  }, [cfg.map_tile_url, cfg.map_attribution]);

  useEffect(() => {
    const m = map.current;
    if (!m || !points.data) return;
    layer.current?.remove();
    const cluster = L.markerClusterGroup({ chunkedLoading: true, showCoverageOnHover: false, maxClusterRadius: 50 });
    const markers = points.data.points.map(([fileId, itemId, lat, lon]) =>
      L.circleMarker([lat, lon], { radius: 6, color: "#fff", weight: 1.5, fillColor: "#4f6bed", fillOpacity: 0.9 }).bindPopup(
        `<img src="${thumbUrl(fileId)}" data-item="${itemId}" alt="" /><a href="#" data-item="${itemId}" onclick="return false">Open details</a>`,
      ),
    );
    cluster.addLayers(markers as unknown as L.Layer[]);
    cluster.addTo(m);
    layer.current = cluster;
    if (markers.length) m.fitBounds(cluster.getBounds(), { padding: [30, 30], maxZoom: 14 });
  }, [points.data, cfg.map_tile_url]);

  return (
    <>
      <div className="page-head">
        <h1>Map</h1>
        <span className="muted">{points.data ? `${points.data.points.length.toLocaleString()} geotagged items` : "Loading…"}</span>
      </div>
      <FilterBar value={filters} onChange={setFilters} facets={facets.data} compact />
      <div ref={el} className="map" />
      {view && <ItemModal itemId={view} onClose={() => setView(null)} />}
    </>
  );
}
