"""Turn an uploaded swing video (or photo) into a handful of JPEG frames."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

import cv2
import numpy as np

MAX_SIDE = 1024           # longest edge of frames sent to the model
THUMB_SIDE = 320          # longest edge of thumbnails returned to the browser
DEFAULT_FRAME_COUNT = 6   # address -> finish, evenly spaced
FALLBACK_MAX_FRAMES = 900 # ~30 s at 30 fps, used only when seeking fails


class VideoError(Exception):
    """The upload could not be turned into frames."""


@dataclass
class Frame:
    time_s: float | None  # None for still photos
    jpeg: bytes
    thumb: bytes


def _fit(img: np.ndarray, side: int) -> np.ndarray:
    h, w = img.shape[:2]
    scale = side / max(h, w)
    if scale >= 1:
        return img
    return cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)


def _encode(img: np.ndarray, quality: int) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise VideoError("A frame could not be encoded as JPEG.")
    return buf.tobytes()


def _to_frame(img: np.ndarray, time_s: float | None) -> Frame:
    return Frame(time_s, _encode(_fit(img, MAX_SIDE), 85), _encode(_fit(img, THUMB_SIDE), 75))


def _positions(total: int, count: int) -> list[int]:
    """Evenly spaced frame indexes, skipping the first and last 5% of the clip."""
    if total <= count:
        return list(range(total))
    points = np.linspace(0.05 * (total - 1), 0.95 * (total - 1), count)
    return sorted({int(round(p)) for p in points})


def image_to_frame(data: bytes) -> Frame:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise VideoError("That image could not be read. Upload a JPG or PNG.")
    return _to_frame(img, None)


def extract_frames(data: bytes, suffix: str = ".mp4", count: int = DEFAULT_FRAME_COUNT) -> list[Frame]:
    fd, path = tempfile.mkstemp(suffix=suffix or ".mp4")
    frames: list[Frame] = []
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)

        cap = cv2.VideoCapture(path)
        try:
            if not cap.isOpened():
                raise VideoError(
                    "That video could not be opened. Upload an MP4 or MOV file."
                )
            fps = cap.get(cv2.CAP_PROP_FPS) or 0
            if not 0 < fps < 1000:
                fps = 30.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

            # Fast path: seek straight to each frame.
            if total > 0:
                for idx in _positions(total, count):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                    ok, img = cap.read()
                    if ok and img is not None:
                        frames.append(_to_frame(img, idx / fps))

            # Slow path: some containers report no frame count or can't seek.
            if not frames:
                cap.release()
                cap = cv2.VideoCapture(path)
                decoded: list[np.ndarray] = []
                while len(decoded) < FALLBACK_MAX_FRAMES:
                    ok, img = cap.read()
                    if not ok or img is None:
                        break
                    decoded.append(_fit(img, MAX_SIDE))
                frames = [_to_frame(decoded[i], i / fps) for i in _positions(len(decoded), count)]
        finally:
            cap.release()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    if not frames:
        raise VideoError(
            "No frames could be read from that video. If it came from an iPhone, "
            "set Camera > Formats to Most Compatible (or export as MP4/H.264) and try again."
        )
    return frames
