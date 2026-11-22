// Where the viewer loads media from. Signed-in pages use the normal API; public share pages swap in
// share-link URLs (which the server checks against the link on every request).
import { createContext, useContext } from "react";

export interface MediaUrls {
  thumb: (fileId: number, size?: "s" | "l") => string;
  full: (fileId: number) => string;
  preview: (fileId: number) => string;
  previewStatus: (fileId: number) => string;
  download: (fileId: number) => string | null;
}

export const internalUrls: MediaUrls = {
  thumb: (id, size = "s") => `/api/files/${id}/thumb?size=${size}`,
  full: (id) => `/api/files/${id}/full`,
  preview: (id) => `/api/files/${id}/preview`,
  previewStatus: (id) => `/api/files/${id}/preview/status`,
  download: (id) => `/api/files/${id}/original`,
};

export function shareUrls(token: string, allowDownload: boolean): MediaUrls {
  const base = `/api/public/s/${encodeURIComponent(token)}/files`;
  return {
    thumb: (id, size = "s") => `${base}/${id}/thumb?size=${size}`,
    full: (id) => `${base}/${id}/full`,
    preview: (id) => `${base}/${id}/preview`,
    previewStatus: (id) => `${base}/${id}/preview/status`,
    download: (id) => (allowDownload ? `${base}/${id}/download` : null),
  };
}

export const MediaUrlsContext = createContext<MediaUrls>(internalUrls);
export const useMediaUrls = () => useContext(MediaUrlsContext);
