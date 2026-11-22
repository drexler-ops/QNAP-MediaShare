"""Find faces in photos and store their fingerprints (runs in worker processes)."""
from __future__ import annotations

import logging
import os

import numpy as np
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Face, File
from ..scanner.thumbs import face_thumb_path, thumb_path

log = logging.getLogger(__name__)

MIN_FACE_PX = 24  # in the 2048px preview; smaller faces are background noise
QUALITY_FACE_PX = 48
QUALITY_SCORE = 0.65
LIVE = ("active", "pending_delete")


def _iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def save_face_thumb(img: Image.Image, box_px: tuple[float, float, float, float], face_id: int) -> None:
    x1, y1, x2, y2 = box_px
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    half = max(x2 - x1, y2 - y1) * 0.75  # a little context around the face
    crop = img.crop((int(cx - half), int(cy - half), int(cx + half), int(cy + half)))
    crop = crop.resize((160, 160), Image.Resampling.LANCZOS)
    path = face_thumb_path(face_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    crop.save(tmp, "WEBP", quality=80)
    os.replace(tmp, path)


def delete_face_thumb(face_id: int) -> None:
    try:
        os.remove(face_thumb_path(face_id))
    except OSError:
        pass


def process_faces(session: Session, file_ids: list[int], eng=None) -> dict[str, int]:
    """Detect + embed faces for these files. Re-running keeps names on faces found again (IoU match)."""
    if eng is None:
        from .engine import engine

        eng = engine()
    stats = {"files": 0, "faces": 0, "skipped": 0, "errors": 0}
    files = list(session.scalars(select(File).where(File.id.in_(file_ids))))
    for f in files:
        if f.kind != "image" or f.status not in LIVE:
            f.face_state = "skip"
            stats["skipped"] += 1
            session.commit()
            continue
        path = thumb_path(f.id, "l")
        if not os.path.isfile(path):
            f.face_state = "skip"  # no preview (unreadable image); retried if it's processed again
            stats["skipped"] += 1
            session.commit()
            continue
        try:
            img = Image.open(path).convert("RGB")
            w, h = img.size
            dets = eng.detect(img)
            old = list(session.scalars(select(Face).where(Face.file_id == f.id)))
            kept_old: set[int] = set()
            new_faces: list[tuple[Face, tuple]] = []
            for d in dets:
                x1, y1, x2, y2 = (float(v) for v in d.box)
                size = min(x2 - x1, y2 - y1)
                if size < MIN_FACE_PX:
                    continue
                vec = np.asarray(eng.embed(img, d.kps), dtype=np.float32)
                bbox = [round(x1 / w, 5), round(y1 / h, 5), round(x2 / w, 5), round(y2 / h, 5)]
                face = Face(
                    file_id=f.id, bbox=bbox, score=round(d.score, 4), size_px=int(size),
                    quality=bool(size >= QUALITY_FACE_PX and d.score >= QUALITY_SCORE),
                    vector=vec.tobytes(), assignment="none",
                )
                # Carry names over from a previous run on the same photo.
                match = max(old, key=lambda o: _iou(o.bbox, bbox), default=None)
                if match is not None and match.id not in kept_old and _iou(match.bbox, bbox) >= 0.5:
                    kept_old.add(match.id)
                    if match.assignment == "manual":
                        face.person_id, face.assignment = match.person_id, "manual"
                    face.rejected = match.rejected
                new_faces.append((face, (x1, y1, x2, y2)))
            for o in old:
                delete_face_thumb(o.id)
                session.delete(o)
            session.flush()
            for face, box in new_faces:
                session.add(face)
            session.flush()
            for face, box in new_faces:
                save_face_thumb(img, box, face.id)
            f.face_state = "done"
            stats["files"] += 1
            stats["faces"] += len(new_faces)
        except Exception as exc:  # one bad image must not stop the batch
            log.warning("face detection failed for %s: %s", f.path, exc)
            session.rollback()
            f = session.get(File, f.id)
            if f is not None:
                f.face_state = "error"
            stats["errors"] += 1
        session.commit()
    return stats
