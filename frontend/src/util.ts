import type { FileInfo, GroupType } from "./api";

export function bytes(n: number | null | undefined): string {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

export function date(iso: string | null | undefined, withTime = true): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return withTime
    ? d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" })
    : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function duration(sec: number | null | undefined): string {
  if (!sec && sec !== 0) return "—";
  const s = Math.round(sec);
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

export function dims(f: Pick<FileInfo, "width" | "height">): string {
  return f.width && f.height ? `${f.width} × ${f.height}` : "—";
}

export function megapixels(f: Pick<FileInfo, "width" | "height">): string {
  return f.width && f.height ? `${((f.width * f.height) / 1e6).toFixed(1)} MP` : "";
}

export function camera(f: Pick<FileInfo, "make" | "model">): string {
  if (!f.make && !f.model) return "—";
  if (f.make && f.model && f.model.toLowerCase().startsWith(f.make.toLowerCase())) return f.model;
  return [f.make, f.model].filter(Boolean).join(" ");
}

export const thumbUrl = (fileId: number, size: "s" | "l" = "s") => `/api/files/${fileId}/thumb?size=${size}`;
export const originalUrl = (fileId: number, download = false) => `/api/files/${fileId}/original${download ? "?download=true" : ""}`;

export const TYPE_LABEL: Record<GroupType, string> = {
  exact: "Exact copies",
  reencode: "Same photo, different file",
  video: "Video duplicates",
  burst: "Similar shots",
};

export const TYPE_HINT: Record<GroupType, string> = {
  exact: "Byte-for-byte identical files.",
  reencode: "The same picture saved again — resized, converted (HEIC→JPG) or sent through a messaging app. May include exact copies.",
  video: "The same clip in different encodings or sizes.",
  burst: "Different shots taken seconds apart. Pick your favourites — nothing is suggested for deletion by default.",
};

export const STATUS_LABEL: Record<string, string> = {
  active: "Active",
  pending_delete: "Awaiting approval",
  quarantined: "In quarantine",
  purged: "Deleted",
  missing: "Missing on disk",
};

export function cls(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}
