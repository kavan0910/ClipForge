"""Face detectors behind one interface. Runs in the OpenCV subprocess only (see ADR-005)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

MODELS = Path.home() / ".cache" / "clipforge" / "models"


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float
    score: float
    eye_y: float | None = None  # mean y of the two eye landmarks (SCRFD only)
    kps: tuple[float, ...] | None = (
        None  # 5 landmarks x,y: left eye, right eye, nose, mouth left, mouth right
    )

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


def iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def nms(boxes: list[Box], thr: float = 0.4) -> list[Box]:
    out: list[Box] = []
    for b in sorted(boxes, key=lambda b: -b.score):
        if all(iou(b, k) < thr for k in out):
            out.append(b)
    return out


class YuNet:
    name = "yunet"

    def __init__(self, score: float = 0.6) -> None:
        import cv2

        self.cv2 = cv2
        self.det = cv2.FaceDetectorYN.create(
            str(MODELS / "yunet.onnx"), "", (320, 320), score, 0.3, 50
        )

    def detect(self, bgr: np.ndarray) -> list[Box]:
        h, w = bgr.shape[:2]
        self.det.setInputSize((w, h))
        _, faces = self.det.detect(bgr)
        if faces is None:
            return []
        return [Box(f[0], f[1], f[2], f[3], float(f[14])) for f in faces]


class Scrfd:
    """SCRFD-500M (InsightFace buffalo_sc) via onnxruntime, decoded here."""

    name = "scrfd500m"
    STRIDES = (8, 16, 32)

    def __init__(self, score: float = 0.5, size: int = 640) -> None:
        import cv2
        import onnxruntime as ort

        self.cv2, self.score, self.size = cv2, score, size
        self.sess = ort.InferenceSession(
            str(MODELS / "buffalo_sc" / "det_500m.onnx"), providers=["CPUExecutionProvider"]
        )
        self.input_name = self.sess.get_inputs()[0].name

    def detect(self, bgr: np.ndarray) -> list[Box]:
        cv2 = self.cv2
        h, w = bgr.shape[:2]
        scale = self.size / max(h, w)
        nw, nh = round(w * scale), round(h * scale)
        canvas = np.zeros((self.size, self.size, 3), dtype=np.uint8)
        canvas[:nh, :nw] = cv2.resize(bgr, (nw, nh))
        blob = cv2.dnn.blobFromImage(
            canvas, 1 / 128.0, (self.size, self.size), (127.5, 127.5, 127.5), swapRB=True
        )
        outs: list = list(self.sess.run(None, {self.input_name: blob}))  # numpy arrays
        boxes: list[Box] = []
        for k, stride in enumerate(self.STRIDES):
            scores = np.asarray(outs[k]).reshape(-1)
            dist = np.asarray(outs[k + 3]).reshape(-1, 4) * stride
            kps = np.asarray(outs[k + 6]).reshape(-1, 10) * stride
            n = self.size // stride
            ys, xs = np.mgrid[0:n, 0:n]
            centers = np.stack([xs, ys], -1).reshape(-1, 2) * stride
            centers = np.repeat(centers, 2, axis=0)  # two anchors per location
            for i in np.where(scores >= self.score)[0]:
                cx, cy = centers[i]
                left, top, right, bottom = dist[i]
                x1, y1, x2, y2 = (
                    (cx - left) / scale,
                    (cy - top) / scale,
                    (cx + right) / scale,
                    (cy + bottom) / scale,
                )
                eye_y = float((cy + (kps[i][1] + kps[i][3]) / 2) / scale)
                pts = tuple(
                    float((cx if j % 2 == 0 else cy) + kps[i][j]) / scale for j in range(10)
                )
                boxes.append(
                    Box(
                        float(x1),
                        float(y1),
                        float(x2 - x1),
                        float(y2 - y1),
                        float(scores[i]),
                        eye_y,
                        pts,
                    )
                )
        return nms(boxes)


def make(name: str):
    return {"yunet": YuNet, "scrfd500m": Scrfd}[name]()
