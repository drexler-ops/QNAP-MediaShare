import { Fragment } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type ItemInfo } from "../api";
import { Loading, Modal } from "../ui";
import { bytes, camera, date, dims, duration, originalUrl, STATUS_LABEL } from "../util";
import { StillView, VideoPlayer } from "./MediaView";

export default function ItemModal({ itemId, onClose }: { itemId: number; onClose: () => void }) {
  const q = useQuery({ queryKey: ["item", itemId], queryFn: () => api<ItemInfo>(`/api/items/${itemId}`) });
  const it = q.data;
  const f = it?.primary;
  return (
    <Modal title={f ? f.name : "Loading…"} onClose={onClose} wide>
      {!it || !f ? (
        <Loading />
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.6fr) minmax(280px, 1fr)", gap: 20 }}>
          <div className="modal-viewer">
            {f.kind === "video" ? <VideoPlayer file={f} /> : <StillView file={f} />}
          </div>
          <div className="stack">
            <dl className="kv">
              <dt>Owner</dt><dd>{f.owner}</dd>
              <dt>Folder</dt><dd className="mono">{f.folder || "/"}</dd>
              <dt>Status</dt><dd>{STATUS_LABEL[f.status] ?? f.status}</dd>
              <dt>Taken</dt><dd>{date(f.taken_at)}</dd>
              <dt>Camera</dt><dd>{camera(f)}</dd>
              {f.lens && (<><dt>Lens</dt><dd>{f.lens}</dd></>)}
              <dt>Dimensions</dt><dd>{dims(f)}</dd>
              {f.kind === "video" && (<><dt>Duration</dt><dd>{duration(f.duration)} · {f.codec ?? "?"}</dd></>)}
              <dt>Format / size</dt><dd>{f.ext.toUpperCase()} · {bytes(f.size)}</dd>
              <dt>Location</dt>
              <dd>
                {f.has_gps ? (
                  <a href={`https://www.openstreetmap.org/?mlat=${f.gps_lat}&mlon=${f.gps_lon}#map=16/${f.gps_lat}/${f.gps_lon}`} target="_blank" rel="noreferrer">
                    {f.gps_lat?.toFixed(5)}, {f.gps_lon?.toFixed(5)}
                  </a>
                ) : "—"}
              </dd>
              <dt>EXIF</dt><dd>{f.has_exif ? `Yes (score ${f.exif_score}/9)` : "None"}</dd>
              {it.is_live_photo && (<><dt>Live Photo</dt><dd>Paired with {it.companion?.name}</dd></>)}
              <dt>Added</dt><dd>{date(f.first_seen_at)}</dd>
            </dl>
            {it.group_ids && it.group_ids.length > 0 && (
              <div className="row">
                <span className="muted small">Duplicate groups:</span>
                {it.group_ids.map((g) => (
                  <Link key={g} to={`/review?group=${g}&status=all`} className="badge accent" onClick={onClose}>
                    #{g}
                  </Link>
                ))}
              </div>
            )}
            {f.exif && (
              <details>
                <summary className="muted small" style={{ cursor: "pointer" }}>All metadata</summary>
                <dl className="kv" style={{ marginTop: 8 }}>
                  {Object.entries(f.exif).map(([k, v]) => (
                    <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>
                  ))}
                </dl>
              </details>
            )}
            {f.status !== "purged" && (
              <div className="row">
                <a className="btn sm" href={originalUrl(f.id)} target="_blank" rel="noreferrer">Open original</a>
                <a className="btn sm" href={originalUrl(f.id, true)}>Download</a>
              </div>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}
