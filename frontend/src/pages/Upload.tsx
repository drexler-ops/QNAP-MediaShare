// Upload photos/videos from this device (PC drag-and-drop, phone/tablet photo picker).
// Files go up in resumable 8 MB chunks, so big videos survive flaky mobile connections.
import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, ApiError, post, type AlbumSummary } from "../api";
import { useViewer } from "../components/MediaViewer";
import { Loading } from "../ui";
import { bytes, cls } from "../util";

interface UploadConfig {
  folder: string | null;
  destination: string | null;
  max_bytes: number;
  chunk_size: number;
  extensions: string[];
}

type Status = "queued" | "uploading" | "finishing" | "saved" | "duplicate" | "error";

interface Job {
  key: string;
  file: File;
  status: Status;
  sent: number;
  message?: string;
  path?: string;
  itemId?: number;
  fileId?: number;
}

const CONCURRENCY = 2;
const isIOS = () =>
  /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** PUT one chunk with upload-progress events. Resolves to the server's byte count. */
function putChunk(id: string, offset: number, blob: Blob, onProgress: (n: number) => void): Promise<number> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/uploads/${id}?offset=${offset}`);
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText).received as number);
      else {
        let detail = xhr.statusText;
        try {
          detail = JSON.parse(xhr.responseText).detail;
        } catch {
          /* not json */
        }
        reject(new ApiError(xhr.status, detail));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error"));
    xhr.send(blob);
  });
}

async function uploadOne(
  file: File,
  albumId: number | null,
  onSent: (n: number) => void,
  onPhase: (s: Status) => void,
): Promise<{ status: "saved" | "duplicate"; path?: string; existing_path?: string; item_id: number; file_id: number }> {
  const start = await post<{ id: string; chunk_size: number }>("/api/uploads", {
    filename: file.name,
    size: file.size,
    last_modified: file.lastModified || null,
    album_id: albumId,
  });
  const { id, chunk_size } = start;
  let offset = 0;
  let failures = 0;
  while (offset < file.size) {
    const end = Math.min(offset + chunk_size, file.size);
    try {
      offset = await putChunk(id, offset, file.slice(offset, end), (n) => onSent(offset + n));
      failures = 0;
      onSent(offset);
    } catch (e) {
      failures++;
      if (failures > 6 || (e instanceof ApiError && [400, 401, 404, 413].includes(e.status))) throw e;
      await sleep(Math.min(30_000, 1000 * 2 ** failures)); // back off, then ask the server where we are
      try {
        offset = (await api<{ received: number }>(`/api/uploads/${id}`)).received;
      } catch {
        /* keep the old offset and retry */
      }
    }
  }
  onPhase("finishing");
  return post(`/api/uploads/${id}/complete`);
}

export default function Upload() {
  const qc = useQueryClient();
  const viewer = useViewer();
  const cfg = useQuery({ queryKey: ["upload-config"], queryFn: () => api<UploadConfig>("/api/uploads/config") });
  const albums = useQuery({ queryKey: ["albums", "editable"], queryFn: () => api<{ albums: AlbumSummary[] }>("/api/albums/editable") });
  const [jobs, setJobs] = useState<Job[]>([]);
  const [albumId, setAlbumId] = useState<string>("");
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const running = useRef(0);
  const albumRef = useRef<number | null>(null);
  albumRef.current = albumId ? Number(albumId) : null;

  const update = useCallback((key: string, patch: Partial<Job>) => {
    setJobs((js) => js.map((j) => (j.key === key ? { ...j, ...patch } : j)));
  }, []);

  const active = jobs.some((j) => j.status === "queued" || j.status === "uploading" || j.status === "finishing");

  // Pump the queue: keep up to CONCURRENCY uploads in flight.
  useEffect(() => {
    if (running.current >= CONCURRENCY) return;
    const next = jobs.find((j) => j.status === "queued");
    if (!next) return;
    running.current++;
    update(next.key, { status: "uploading" });
    uploadOne(
      next.file,
      albumRef.current,
      (n) => update(next.key, { sent: n }),
      (s) => update(next.key, { status: s }),
    )
      .then((r) =>
        update(next.key, {
          status: r.status,
          sent: next.file.size,
          path: r.status === "saved" ? r.path : r.existing_path,
          itemId: r.item_id,
          fileId: r.file_id,
        }),
      )
      .catch((e) => update(next.key, { status: "error", message: e instanceof Error ? e.message : String(e) }))
      .finally(() => {
        running.current--;
        setJobs((js) => [...js]); // re-run the pump
        qc.invalidateQueries({ queryKey: ["items"] });
        qc.invalidateQueries({ queryKey: ["album"] });
      });
  }, [jobs, update, qc]);

  useEffect(() => {
    if (!active) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [active]);

  const addFiles = (list: FileList | File[]) => {
    const exts = new Set(cfg.data?.extensions ?? []);
    const added: Job[] = Array.from(list).map((file, i) => {
      const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
      const key = `${Date.now()}-${i}-${file.name}`;
      if (!exts.has(ext)) return { key, file, status: "error", sent: 0, message: "Not a photo or video" };
      if (cfg.data && file.size > cfg.data.max_bytes) return { key, file, status: "error", sent: 0, message: "Too large" };
      return { key, file, status: "queued", sent: 0 };
    });
    setJobs((js) => [...added, ...js]);
  };

  const retry = (j: Job) => update(j.key, { status: "queued", sent: 0, message: undefined });
  const clearDone = () => setJobs((js) => js.filter((j) => !["saved", "duplicate"].includes(j.status)));

  if (cfg.isLoading) return <Loading />;
  const c = cfg.data;
  const counts = {
    saved: jobs.filter((j) => j.status === "saved").length,
    duplicate: jobs.filter((j) => j.status === "duplicate").length,
    error: jobs.filter((j) => j.status === "error").length,
    pending: jobs.filter((j) => ["queued", "uploading", "finishing"].includes(j.status)).length,
  };
  const totalBytes = jobs.filter((j) => j.status !== "error").reduce((n, j) => n + j.file.size, 0);
  const sentBytes = jobs.filter((j) => j.status !== "error").reduce((n, j) => n + Math.min(j.sent, j.file.size), 0);

  return (
    <>
      <div className="page-head">
        <h1>Upload</h1>
        <span className="muted">Add photos and videos from this device</span>
      </div>

      {!c?.folder ? (
        <div className="card empty">
          <h2>You don't have a folder yet</h2>
          <p>Uploads are saved into your own folder on the NAS. Ask an admin to set one for you under Settings → Users.</p>
        </div>
      ) : (
        <>
          {isIOS() && (
            <div className="banner info" style={{ marginBottom: 14 }}>
              <span>
                <strong>iPhone/iPad tip:</strong> in the photo picker, tap <strong>Options</strong> and turn on <strong>Location</strong>{" "}
                to keep where photos were taken. Live Photos upload as still photos (the motion part isn't sent by Safari).
              </span>
            </div>
          )}

          <div
            className={cls("dropzone", dragging && "over")}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
            }}
            onClick={() => input.current?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
          >
            <div className="dropzone-icon">⬆</div>
            <strong>Choose photos &amp; videos</strong>
            <span className="muted small">or drag them here · up to {bytes(c.max_bytes)} each</span>
            <span className="faint small">
              Saved to <code>{c.destination}</code>, by month taken. Files already on the NAS are skipped.
            </span>
            <input
              ref={input}
              type="file"
              multiple
              hidden
              accept={["image/*", "video/*", ...c.extensions.map((e) => `.${e}`)].join(",")}
              onChange={(e) => {
                if (e.target.files?.length) addFiles(e.target.files);
                e.target.value = "";
              }}
            />
          </div>

          <div className="row" style={{ margin: "14px 0" }}>
            <label className="field" style={{ minWidth: 240 }}>
              Also add new uploads to album
              <select value={albumId} onChange={(e) => setAlbumId(e.target.value)}>
                <option value="">— none —</option>
                {albums.data?.albums.map((a) => (
                  <option key={a.id} value={a.id}>{a.title}</option>
                ))}
              </select>
            </label>
            <div className="grow" />
            {jobs.length > 0 && (
              <div className="small muted" style={{ textAlign: "right" }}>
                {counts.saved} saved · {counts.duplicate} already on NAS · {counts.error} failed
                {counts.pending > 0 && ` · ${counts.pending} to go`}
                <div className="progress" style={{ width: 240, marginTop: 6 }}>
                  <div style={{ width: `${totalBytes ? (sentBytes / totalBytes) * 100 : 0}%` }} />
                </div>
              </div>
            )}
          </div>

          {jobs.length > 0 && (
            <div className="card">
              <div className="row" style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)" }}>
                <strong className="grow">This session</strong>
                {counts.saved > 0 && !active && <Link to="/browse" className="btn sm">See in Browse</Link>}
                <button className="btn sm ghost" onClick={clearDone} disabled={!counts.saved && !counts.duplicate}>Clear finished</button>
              </div>
              <div className="upload-list">
                {jobs.map((j) => {
                  const pct = j.file.size ? Math.round((Math.min(j.sent, j.file.size) / j.file.size) * 100) : 0;
                  return (
                    <div key={j.key} className={cls("upload-row", j.status)}>
                      <div className="grow" style={{ minWidth: 0 }}>
                        <div className="upload-name" title={j.file.name}>{j.file.name}</div>
                        <div className="small muted">
                          {bytes(j.file.size)}
                          {j.status === "uploading" && ` · ${pct}%`}
                          {j.status === "finishing" && " · checking for duplicates…"}
                          {j.status === "saved" && <> · saved to <code>{j.path}</code></>}
                          {j.status === "duplicate" && <> · already on the NAS at <code>{j.path}</code></>}
                          {j.status === "error" && ` · ${j.message}`}
                        </div>
                        {(j.status === "uploading" || j.status === "finishing") && (
                          <div className="progress" style={{ marginTop: 6 }}><div style={{ width: `${pct}%` }} /></div>
                        )}
                      </div>
                      <span className={cls("badge", j.status === "saved" && "keep", j.status === "duplicate" && "warn", j.status === "error" && "delete")}>
                        {{ queued: "Waiting", uploading: "Uploading", finishing: "Finishing", saved: "Saved", duplicate: "Skipped", error: "Failed" }[j.status]}
                      </span>
                      {(j.status === "saved" || j.status === "duplicate") && j.itemId && (
                        <button className="btn sm" onClick={() => viewer.open([{ item: j.itemId!, file: j.fileId }])}>View</button>
                      )}
                      {j.status === "error" && j.message !== "Not a photo or video" && j.message !== "Too large" && (
                        <button className="btn sm" onClick={() => retry(j)}>Retry</button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
    </>
  );
}
