"""Face detection (SCRFD) and recognition (ArcFace) with InsightFace's buffalo_l ONNX models.

Runs the models directly with onnxruntime (no compiled `insightface` package needed).
The models are downloaded on first use into MODELS_ROOT and verified by SHA-256.
InsightFace's pretrained models are licensed for non-commercial use.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import threading
import urllib.request
import zipfile
from dataclasses import dataclass

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

MODEL_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
MODEL_SHA256 = "80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f"
DET_FILE = "det_10g.onnx"
REC_FILE = "w600k_r50.onnx"

DET_SIZE = 640
DET_THRESHOLD = 0.5
NMS_THRESHOLD = 0.4
STRIDES = (8, 16, 32)

# Canonical 5-point landmark positions for a 112x112 ArcFace crop (eyes, nose, mouth corners).
ARCFACE_DST = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]],
    dtype=np.float32,
)


def models_dir() -> str:
    return os.environ.get("MODELS_ROOT", "/models")


def models_ready() -> bool:
    d = os.path.join(models_dir(), "buffalo_l")
    return all(os.path.isfile(os.path.join(d, f)) for f in (DET_FILE, REC_FILE))


_download_lock = threading.Lock()


class _file_lock:
    """Cross-process lock so several worker processes don't download the models at once."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.fh = None

    def __enter__(self):
        self.fh = open(self.path, "a+")
        try:
            import fcntl

            fcntl.flock(self.fh, fcntl.LOCK_EX)
        except ImportError:  # Windows dev machines: in-process lock only
            pass
        return self

    def __exit__(self, *exc) -> None:
        try:
            import fcntl

            fcntl.flock(self.fh, fcntl.LOCK_UN)
        except ImportError:
            pass
        self.fh.close()


def ensure_models() -> str:
    """Download + verify buffalo_l once; keep only the two models we use. Returns the model folder."""
    target = os.path.join(models_dir(), "buffalo_l")
    if models_ready():
        return target
    os.makedirs(models_dir(), exist_ok=True)
    with _download_lock, _file_lock(os.path.join(models_dir(), ".download.lock")):
        if models_ready():
            return target
        log.info("downloading face models from %s (~290 MB, one time)", MODEL_URL)
        fd, tmp = tempfile.mkstemp(prefix="buffalo_l-", suffix=".zip", dir=models_dir())
        os.close(fd)
        try:
            h = hashlib.sha256()
            with urllib.request.urlopen(MODEL_URL, timeout=60) as resp, open(tmp, "wb") as out:
                while chunk := resp.read(1 << 20):
                    h.update(chunk)
                    out.write(chunk)
            if h.hexdigest() != MODEL_SHA256:
                raise RuntimeError("face model download failed verification (checksum mismatch)")
            staging = target + ".tmp"
            shutil.rmtree(staging, ignore_errors=True)
            os.makedirs(staging)
            with zipfile.ZipFile(tmp) as z:
                for name in (DET_FILE, REC_FILE):
                    member = next(m for m in z.namelist() if os.path.basename(m) == name)
                    with z.open(member) as src, open(os.path.join(staging, name), "wb") as dst:
                        shutil.copyfileobj(src, dst)
            shutil.rmtree(target, ignore_errors=True)
            os.replace(staging, target)
            log.info("face models ready in %s", target)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    return target


# --- maths ------------------------------------------------------------------------------------


def nms(boxes: np.ndarray, scores: np.ndarray, thresh: float) -> list[int]:
    x1, y1, x2, y2 = boxes.T
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1 + 1) * np.maximum(0.0, yy2 - yy1 + 1)
        iou = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[1:][iou <= thresh]
    return keep


def similarity_transform(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Least-squares similarity (rotation + uniform scale + translation) mapping src -> dst (Umeyama). 2x3."""
    n = src.shape[0]
    mu_s, mu_d = src.mean(0), dst.mean(0)
    s_c, d_c = src - mu_s, dst - mu_d
    cov = d_c.T @ s_c / n
    u, sig, vt = np.linalg.svd(cov)
    d = np.ones(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        d[-1] = -1
    r = u @ np.diag(d) @ vt
    var_s = (s_c**2).sum() / n
    scale = (sig * d).sum() / var_s
    t = mu_d - scale * r @ mu_s
    return np.hstack([scale * r, t[:, None]]).astype(np.float64)


def align_face(img: Image.Image, kps: np.ndarray, size: int = 112) -> Image.Image:
    m = similarity_transform(kps.astype(np.float64), ARCFACE_DST.astype(np.float64) * (size / 112.0))
    a = np.vstack([m, [0, 0, 1]])
    inv = np.linalg.inv(a)  # PIL wants output -> input mapping
    coeffs = (inv[0, 0], inv[0, 1], inv[0, 2], inv[1, 0], inv[1, 1], inv[1, 2])
    return img.transform((size, size), Image.Transform.AFFINE, coeffs, resample=Image.Resampling.BILINEAR)


@dataclass
class DetectedFace:
    box: np.ndarray  # x1, y1, x2, y2 in image pixels
    score: float
    kps: np.ndarray  # 5 x 2


class FaceEngine:
    """Thread-safe-enough per process; create once per worker process (models load lazily)."""

    def __init__(self) -> None:
        import onnxruntime as ort

        folder = ensure_models()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = int(os.environ.get("FACE_THREADS", "2"))
        opts.inter_op_num_threads = 1
        self.det = ort.InferenceSession(os.path.join(folder, DET_FILE), opts, providers=["CPUExecutionProvider"])
        self.rec = ort.InferenceSession(os.path.join(folder, REC_FILE), opts, providers=["CPUExecutionProvider"])
        self.det_input = self.det.get_inputs()[0].name
        self.rec_input = self.rec.get_inputs()[0].name
        self._anchor_cache: dict[tuple[int, int, int], np.ndarray] = {}

    def _anchors(self, h: int, w: int, stride: int, num_anchors: int) -> np.ndarray:
        key = (h, w, stride)
        if key not in self._anchor_cache:
            centers = np.stack(np.mgrid[:h, :w][::-1], axis=-1).astype(np.float32)
            centers = (centers * stride).reshape(-1, 2)
            if num_anchors > 1:
                centers = np.stack([centers] * num_anchors, axis=1).reshape(-1, 2)
            self._anchor_cache[key] = centers
        return self._anchor_cache[key]

    def detect(self, img: Image.Image) -> list[DetectedFace]:
        img = img.convert("RGB")
        w, h = img.size
        if h / w > 1:
            new_h, new_w = DET_SIZE, max(1, int(DET_SIZE * w / h))
        else:
            new_w, new_h = DET_SIZE, max(1, int(DET_SIZE * h / w))
        scale = new_h / h
        canvas = np.zeros((DET_SIZE, DET_SIZE, 3), dtype=np.float32)
        canvas[:new_h, :new_w] = np.asarray(img.resize((new_w, new_h), Image.Resampling.BILINEAR), dtype=np.float32)
        blob = ((canvas - 127.5) / 128.0).transpose(2, 0, 1)[None]
        outs = self.det.run(None, {self.det_input: blob})
        fmc = len(STRIDES)
        num_anchors = 2 if len(outs) in (6, 9) else 1
        boxes, scores, kpss = [], [], []
        for i, stride in enumerate(STRIDES):
            sc = outs[i].reshape(-1)
            bb = outs[i + fmc].reshape(-1, 4) * stride
            kp = outs[i + 2 * fmc].reshape(-1, 10) * stride
            anchors = self._anchors(DET_SIZE // stride, DET_SIZE // stride, stride, num_anchors)
            pos = np.where(sc >= DET_THRESHOLD)[0]
            if not pos.size:
                continue
            c = anchors[pos]
            b = bb[pos]
            boxes.append(np.stack([c[:, 0] - b[:, 0], c[:, 1] - b[:, 1], c[:, 0] + b[:, 2], c[:, 1] + b[:, 3]], -1))
            k = kp[pos]
            kpss.append(np.stack([c[:, j % 2] + k[:, j] for j in range(10)], -1).reshape(-1, 5, 2))
            scores.append(sc[pos])
        if not boxes:
            return []
        all_boxes = np.concatenate(boxes) / scale
        all_scores = np.concatenate(scores)
        all_kps = np.concatenate(kpss) / scale
        keep = nms(all_boxes, all_scores, NMS_THRESHOLD)
        out = []
        for i in keep:
            box = all_boxes[i].copy()
            box[[0, 2]] = box[[0, 2]].clip(0, w)
            box[[1, 3]] = box[[1, 3]].clip(0, h)
            out.append(DetectedFace(box=box, score=float(all_scores[i]), kps=all_kps[i]))
        return out

    def embed(self, img: Image.Image, kps: np.ndarray) -> np.ndarray:
        crop = align_face(img.convert("RGB"), kps)
        blob = ((np.asarray(crop, dtype=np.float32) - 127.5) / 127.5).transpose(2, 0, 1)[None]
        vec = self.rec.run(None, {self.rec_input: blob})[0].reshape(-1).astype(np.float32)
        return vec / (np.linalg.norm(vec) + 1e-9)


_engine: FaceEngine | None = None


def engine() -> FaceEngine:
    global _engine
    if _engine is None:
        _engine = FaceEngine()
    return _engine
