"""Golf Swing Coach web service.

GET  /                                   web page for uploading a swing
GET  /management/health/liveness         process is up
GET  /management/health/readiness        200 when configured, 503 otherwise
POST /api/golf-swing/analyze             multipart upload -> coaching JSON
"""

from __future__ import annotations

import base64
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse

from app import coach, video

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("golf_swing")

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_CONCERNS_CHARS = 500
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".webm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

@asynccontextmanager
async def lifespan(_app: FastAPI):
    problems = coach.config_problems()
    if problems:
        log.warning("Not ready: %s", " ".join(problems))
    else:
        s = coach.settings()
        log.info("Ready (provider=%s, model=%s, prompt_sha256=%s)", s.provider, s.model, coach.PROMPT_SHA256)
    yield


app = FastAPI(title="Golf Swing Coach", version="1.0.0", lifespan=lifespan)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/management/health/liveness")
def liveness() -> dict:
    return {"status": "ok"}


@app.get("/management/health/readiness")
def readiness():
    s = coach.settings()
    problems = coach.config_problems()
    body = {
        "status": "ok" if not problems else "not_ready",
        "provider": s.provider,
        "model": s.model,
        "prompt_sha256": coach.PROMPT_SHA256,
    }
    if problems:
        body["problems"] = problems
        return JSONResponse(body, status_code=503)
    return body


@app.post("/api/golf-swing/analyze")
async def analyze(
    file: UploadFile = File(...),
    swing_type: str = Form("Driver"),
    skill_level: str = Form("Beginner"),
    concerns: str = Form(""),
):
    if swing_type not in coach.SWING_TYPES:
        raise HTTPException(422, f"swing_type must be one of: {', '.join(coach.SWING_TYPES)}.")
    if skill_level not in coach.SKILL_LEVELS:
        raise HTTPException(422, f"skill_level must be one of: {', '.join(coach.SKILL_LEVELS)}.")
    concerns = concerns.strip()
    if len(concerns) > MAX_CONCERNS_CHARS:
        raise HTTPException(422, f"Concerns must be {MAX_CONCERNS_CHARS} characters or fewer.")

    problems = coach.config_problems()
    if problems:
        raise HTTPException(503, "The service isn't configured yet: " + " ".join(problems))

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(400, "The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "That file is over 100 MB. Trim the clip to just the swing and try again.")

    suffix = Path(file.filename or "").suffix.lower()
    ctype = (file.content_type or "").lower()
    is_video = ctype.startswith("video/") or suffix in VIDEO_EXTS
    is_image = ctype.startswith("image/") or suffix in IMAGE_EXTS

    try:
        if is_video:
            frames = await run_in_threadpool(video.extract_frames, data, suffix if suffix in VIDEO_EXTS else ".mp4")
        elif is_image:
            frames = [await run_in_threadpool(video.image_to_frame, data)]
        else:
            raise HTTPException(415, "Upload a video (MP4, MOV) or a photo (JPG, PNG).")
    except video.VideoError as exc:
        raise HTTPException(422, str(exc)) from exc

    log.info("Analysing %s: %d frame(s), %s, %s", file.filename, len(frames), swing_type, skill_level)
    try:
        result = await coach.analyze(frames, swing_type, skill_level, concerns)
    except coach.CoachError as exc:
        log.error("Analysis failed: %s", exc)
        raise HTTPException(502, str(exc)) from exc

    return {
        "result": result.model_dump(),
        "frames": [
            {
                "number": n,
                "time_s": f.time_s,
                "thumbnail": "data:image/jpeg;base64," + base64.b64encode(f.thumb).decode("ascii"),
            }
            for n, f in enumerate(frames, start=1)
        ],
    }
