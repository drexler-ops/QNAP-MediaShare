// Thin fetch wrapper + the JSON shapes returned by the backend.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = unknown>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(path, {
    credentials: "same-origin",
    ...rest,
    headers: { ...(json !== undefined ? { "Content-Type": "application/json" } : {}), ...(rest.headers ?? {}) },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const post = <T = unknown>(path: string, json?: unknown) => api<T>(path, { method: "POST", json: json ?? {} });

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export interface AppConfig {
  app_name: string;
  map_tile_url: string | null;
  map_attribution: string;
  share_only?: boolean;
  share_base_url?: string | null;
}

export const DEFAULT_CONFIG: AppConfig = {
  app_name: "Photo Audit",
  map_tile_url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
  map_attribution: "© OpenStreetMap contributors",
};

export interface AlbumSummary {
  id: number;
  title: string;
  description: string | null;
  owner: string | null;
  owner_id: number;
  role: "owner" | "editor" | "viewer";
  count: number;
  cover_file_id: number | null;
  shared_with: number;
  active_links: number;
  updated_at: string | null;
}

export interface ShareLinkInfo {
  id: number;
  url: string;
  token: string;
  state: "active" | "expired" | "revoked";
  created_by: string | null;
  created_at: string | null;
  expires_at: string | null;
  has_password: boolean;
  allow_download: boolean;
  view_count: number;
  last_viewed_at: string | null;
}

export interface AlbumDetail extends AlbumSummary {
  cover_item_id: number | null;
  items: (ItemInfo & { added_by: string | null; added_at: string | null })[];
  members: { user_id: number; name: string | null; role: "viewer" | "editor" }[];
  links: ShareLinkInfo[];
}

export interface PublicItem {
  item_id: number;
  file_id: number;
  kind: "image" | "video";
  name: string;
  ext: string;
  width: number | null;
  height: number | null;
  duration: number | null;
  size: number;
  taken_at: string | null;
  camera: string | null;
  thumb: boolean;
  live_file_id: number | null;
}

export interface PublicAlbum {
  app_name: string;
  title: string;
  description: string | null;
  allow_download: boolean;
  expires_at: string | null;
  items: PublicItem[];
}

export interface Me {
  id: number;
  name: string;
  home_folder: string | null;
  is_admin: boolean;
}

export interface UserRow {
  id: number;
  name: string;
  home_folder: string | null;
  is_admin: boolean;
  is_active: boolean;
  must_set_password: boolean;
  files: number;
}

export interface FileInfo {
  id: number;
  item_id: number;
  role: "primary" | "live_video";
  path: string;
  original_path: string | null;
  name: string;
  folder: string;
  ext: string;
  kind: "image" | "video";
  size: number;
  width: number | null;
  height: number | null;
  duration: number | null;
  codec: string | null;
  taken_at: string | null;
  make: string | null;
  model: string | null;
  lens: string | null;
  gps_lat: number | null;
  gps_lon: number | null;
  has_gps: boolean;
  has_exif: boolean;
  exif_score: number;
  exif: Record<string, unknown> | null;
  status: "active" | "pending_delete" | "quarantined" | "purged" | "missing";
  owner_id: number | null;
  owner: string;
  thumb: boolean;
  process_state: string;
  process_error: string | null;
  first_seen_at: string | null;
  quarantined_at: string | null;
}

export interface ItemInfo {
  id: number;
  primary: FileInfo;
  companion: FileInfo | null;
  is_live_photo: boolean;
  owner_id: number | null;
  owner: string;
  in_group?: boolean;
  group_ids?: number[];
}

export type GroupType = "exact" | "reencode" | "burst" | "video";

export interface DecisionInfo {
  id: number;
  action: "keep" | "delete" | "skip";
  state: string;
  marked_by: string | null;
  marked_at: string | null;
  approved_by: string | null;
}

export interface GroupMember extends ItemInfo {
  match_type: string;
  suggested_keep: boolean;
  score: number;
  score_breakdown: { parts: Record<string, number>; notes: string[] } | null;
  decision: DecisionInfo | null;
  deleting_elsewhere: boolean;
  other_groups: number[];
  albums: number;
}

export interface GroupDetail {
  id: number;
  type: GroupType;
  status: "open" | "resolved" | "dismissed";
  confidence: number;
  members: GroupMember[];
}

export interface GroupCard {
  id: number;
  type: GroupType;
  status: string;
  confidence: number;
  size: number;
  reclaim_bytes: number;
  members: { item_id: number; file_id: number | null; suggested_keep: boolean; owner: string | null; status: string | null }[];
}

export interface Summary {
  by_type: Record<GroupType, { open: number; resolved: number; dismissed: number }>;
  pending_approvals: number;
  bulk_exact: number;
}

export interface PendingRow {
  decision_id: number;
  group_id: number | null;
  marked_by: string | null;
  marked_at: string | null;
  item: ItemInfo;
  kept: ItemInfo | null;
}

export interface QuarantineRow {
  item: ItemInfo;
  quarantined_at: string | null;
  purge_after: string | null;
  eligible: boolean;
  marked_by: string | null;
  approved_by: string | null;
}

export interface Settings {
  reencode_threshold: number;
  burst_threshold: number;
  burst_window_seconds: number;
  video_duration_tolerance: number;
  video_frame_threshold: number;
  keeper_weights: Record<"exif" | "gps" | "resolution" | "format" | "size", number>;
  scan_interval_minutes: number;
  scans_paused: boolean;
  purge_days: number;
  auto_purge: boolean;
  faces_enabled: boolean;
  face_auto_threshold: number;
  face_suggest_threshold: number;
  face_cluster_threshold: number;
  face_min_cluster: number;
}

export interface Facets {
  owners: { id: number; name: string }[];
  cameras: { model: string; count: number }[];
  date_min: string | null;
  date_max: string | null;
}

export interface PersonSummary {
  id: number;
  name: string | null;
  hidden: boolean;
  cover_face_id: number | null;
  faces: number;
  photos: number;
}

export interface PeopleList {
  named: PersonSummary[];
  unnamed: PersonSummary[];
  unnamed_total: number;
  unnamed_page: number;
  unnamed_page_size: number;
  review: number;
  status: { enabled: boolean; models_ready: boolean; photos_total: number; photos_done: number; faces: number; regrouping?: boolean };
}

export interface PersonDetail extends PersonSummary {
  face_list: { face_id: number; file_id: number; item_id: number; assignment: string; similarity: number | null }[];
  items: ItemInfo[];
  items_total: number;
  page: number;
  page_size: number;
  faces_total: number;
  faces_page: number;
  faces_page_size: number;
  regrouping?: boolean;
}

export interface ItemFace {
  face_id: number;
  bbox: [number, number, number, number];
  person_id: number | null;
  name: string | null;
  assignment: "none" | "cluster" | "auto" | "manual";
  suggested_id: number | null;
  suggested_name: string | null;
}

export interface ReviewFace {
  face_id: number;
  file_id: number;
  item_id: number;
  suggested_id: number;
  suggested_name: string;
  similarity: number;
}

export const faceThumbUrl = (faceId: number) => `/api/faces/${faceId}/thumb`;
