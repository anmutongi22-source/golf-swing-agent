"""Calls Claude (via Azure AI Foundry or the Anthropic API) and parses the coaching reply."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.video import Frame

SWING_TYPES = ("Driver", "Iron", "Wedge", "Chip", "Putting")
SKILL_LEVELS = ("Beginner", "Intermediate", "Advanced")

PROMPT = (Path(__file__).parent / "prompts" / "coach.md").read_text(encoding="utf-8")
PROMPT_SHA256 = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()

ANTHROPIC_VERSION = "2023-06-01"
REQUEST_TIMEOUT_S = 120

# Tests swap this for an httpx.MockTransport so no real calls are made.
_transport: httpx.AsyncBaseTransport | None = None


class CoachError(Exception):
    """The model call failed or returned something unusable."""


# ---------------------------------------------------------------------------
# Settings (read from environment variables / Azure App Service app settings)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    api_key: str
    endpoint: str


def settings() -> Settings:
    provider = os.getenv("MODEL_PROVIDER", "foundry").strip().lower()
    model = os.getenv("MODEL_ID", "claude-haiku-4-5").strip()

    if provider == "anthropic":
        return Settings(provider, model, os.getenv("ANTHROPIC_API_KEY", "").strip(),
                        "https://api.anthropic.com/v1/messages")

    resource = os.getenv("FOUNDRY_RESOURCE", "").strip()
    endpoint = os.getenv("FOUNDRY_ENDPOINT", "").strip()
    if not endpoint and resource:
        endpoint = f"https://{resource}.services.ai.azure.com/anthropic/v1/messages"
    return Settings(provider, model, os.getenv("FOUNDRY_API_KEY", "").strip(), endpoint)


def config_problems() -> list[str]:
    s = settings()
    if s.provider not in ("foundry", "anthropic"):
        return [f"MODEL_PROVIDER must be 'foundry' or 'anthropic', not '{s.provider}'."]
    problems = []
    if not s.model:
        problems.append("MODEL_ID is not set.")
    if not s.api_key:
        problems.append("FOUNDRY_API_KEY is not set." if s.provider == "foundry"
                        else "ANTHROPIC_API_KEY is not set.")
    if not s.endpoint:
        problems.append("FOUNDRY_RESOURCE (or FOUNDRY_ENDPOINT) is not set.")
    return problems


# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------

class Scores(BaseModel):
    overall: int = Field(ge=0, le=100)
    technique: int = Field(ge=0, le=100)
    consistency: int = Field(ge=0, le=100)


class Note(BaseModel):
    text: str
    frame: int | None = None


class Drill(BaseModel):
    name: str
    description: str


class SwingAnalysis(BaseModel):
    scores: Scores
    summary: str
    strengths: list[Note]
    improvements: list[Note]
    drills: list[Drill]
    main_tip: str


SCHEMA = """{
  "scores": {"overall": <0-100>, "technique": <0-100>, "consistency": <0-100>},
  "summary": "<2-3 sentences: what you see overall, plus any camera-angle caveat>",
  "strengths": [{"text": "<specific observation>", "frame": <frame number or null>}],
  "improvements": [{"text": "<specific fix>", "frame": <frame number or null>}],
  "drills": [{"name": "<drill name>", "description": "<2-sentence how-to>"}],
  "main_tip": "<the single most important thing to work on, 1-2 sentences>"
}"""


# ---------------------------------------------------------------------------
# Request / response
# ---------------------------------------------------------------------------

def build_content(frames: list[Frame], swing_type: str, skill_level: str, concerns: str) -> list[dict]:
    content: list[dict] = []
    for n, frame in enumerate(frames, start=1):
        label = f"Frame {n}" + (f" at {frame.time_s:.2f} s" if frame.time_s is not None else " (photo)")
        content.append({"type": "text", "text": label})
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": base64.b64encode(frame.jpeg).decode("ascii")},
        })

    kind = "video, shown as the frames above" if frames[0].time_s is not None else "photo"
    ask = (
        f"Review this golf swing ({kind}).\n"
        f"Club / shot: {swing_type}\n"
        f"Golfer's level: {skill_level}\n"
    )
    if concerns:
        ask += f"Golfer's concerns: {concerns}\n"
    ask += (
        "\nGive 2-4 strengths, 2-4 improvements, and 3 drills.\n"
        f"Reply with JSON only, in exactly this shape:\n{SCHEMA}"
    )
    content.append({"type": "text", "text": ask})
    return content


def parse_reply(text: str, frame_count: int) -> SwingAnalysis:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise CoachError("The model did not return JSON.")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise CoachError(f"The model returned malformed JSON: {exc.msg}.") from exc
    try:
        result = SwingAnalysis.model_validate(data)
    except ValidationError as exc:
        raise CoachError(f"The model's JSON didn't match the expected shape: {exc.error_count()} problem(s).") from exc

    # Drop frame references that point at frames we didn't send.
    for note in (*result.strengths, *result.improvements):
        if note.frame is not None and not 1 <= note.frame <= frame_count:
            note.frame = None
    return result


async def analyze(frames: list[Frame], swing_type: str, skill_level: str, concerns: str) -> SwingAnalysis:
    if not frames:
        raise CoachError("No frames to analyse.")
    s = settings()
    body = {
        "model": s.model,
        "max_tokens": 1500,
        "system": PROMPT,
        "messages": [{"role": "user", "content": build_content(frames, swing_type, skill_level, concerns)}],
    }
    headers = {
        "content-type": "application/json",
        "anthropic-version": ANTHROPIC_VERSION,
        "x-api-key": s.api_key,
    }
    if s.provider == "foundry":
        headers["api-key"] = s.api_key  # Azure-style header; harmless if unused

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S, transport=_transport) as client:
            resp = await client.post(s.endpoint, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise CoachError(f"Could not reach the model endpoint ({type(exc).__name__}).") from exc

    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", {}).get("message") or resp.text[:300]
        except ValueError:
            detail = resp.text[:300]
        raise CoachError(f"The model endpoint returned HTTP {resp.status_code}: {detail}")

    try:
        blocks = resp.json().get("content", [])
    except ValueError as exc:
        raise CoachError("The model endpoint returned a non-JSON response.") from exc
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    return parse_reply(text, len(frames))
