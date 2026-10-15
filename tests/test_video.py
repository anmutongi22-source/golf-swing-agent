import os
import tempfile

import cv2
import numpy as np
import pytest

from app import video


def make_video(frames: int = 60, size=(480, 640), fps: float = 30.0) -> bytes:
    """Write a small synthetic MP4 where each frame has a different brightness."""
    h, w = size
    fd, path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    try:
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        assert writer.isOpened(), "OpenCV could not open a video writer"
        for i in range(frames):
            img = np.full((h, w, 3), int(255 * i / max(frames - 1, 1)), np.uint8)
            cv2.putText(img, str(i), (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 255), 3)
            writer.write(img)
        writer.release()
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.remove(path)


def test_extracts_six_frames_in_time_order():
    frames = video.extract_frames(make_video(frames=90), ".mp4")
    assert len(frames) == 6
    times = [f.time_s for f in frames]
    assert times == sorted(times)
    assert 0 < times[0] < times[-1] < 3.0


def test_frames_are_valid_jpegs_and_resized():
    frames = video.extract_frames(make_video(frames=30, size=(1440, 1920)), ".mp4")
    for f in frames:
        img = cv2.imdecode(np.frombuffer(f.jpeg, np.uint8), cv2.IMREAD_COLOR)
        thumb = cv2.imdecode(np.frombuffer(f.thumb, np.uint8), cv2.IMREAD_COLOR)
        assert max(img.shape[:2]) == video.MAX_SIDE
        assert max(thumb.shape[:2]) == video.THUMB_SIDE


def test_short_clip_returns_every_frame():
    frames = video.extract_frames(make_video(frames=4), ".mp4")
    assert len(frames) == 4


def test_garbage_video_raises_friendly_error():
    with pytest.raises(video.VideoError):
        video.extract_frames(b"this is not a video", ".mp4")


def test_image_to_frame_reads_png():
    ok, buf = cv2.imencode(".png", np.zeros((200, 300, 3), np.uint8))
    frame = video.image_to_frame(buf.tobytes())
    assert frame.time_s is None
    assert frame.jpeg[:2] == b"\xff\xd8"


def test_bad_image_raises_friendly_error():
    with pytest.raises(video.VideoError):
        video.image_to_frame(b"not an image")
