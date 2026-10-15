import json

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import coach
from app.main import app
from tests.test_video import make_video

GOOD_REPLY = {
    "scores": {"overall": 71, "technique": 68, "consistency": 74},
    "summary": "Solid setup; the club gets across the line at the top.",
    "strengths": [{"text": "Balanced address posture.", "frame": 1}],
    "improvements": [
        {"text": "Shaft points left of target at the top.", "frame": 3},
        {"text": "Reference to a frame that doesn't exist.", "frame": 99},
    ],
    "drills": [{"name": "Pump drill", "description": "Swing to the top and pause. Pump down twice, then hit."}],
    "main_tip": "Keep the club on plane at the top.",
}


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "foundry")
    monkeypatch.setenv("FOUNDRY_RESOURCE", "test-resource")
    monkeypatch.setenv("FOUNDRY_API_KEY", "test-key")
    monkeypatch.setenv("MODEL_ID", "claude-haiku-4-5")
    monkeypatch.delenv("FOUNDRY_ENDPOINT", raising=False)


@pytest.fixture
def model_reply(monkeypatch):
    """Route model calls to a fake endpoint; returns the list of captured requests."""
    calls = []
    state = {"status": 200, "text": json.dumps(GOOD_REPLY)}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if state["status"] != 200:
            return httpx.Response(state["status"], json={"error": {"message": "nope"}})
        return httpx.Response(200, json={"content": [{"type": "text", "text": state["text"]}]})

    monkeypatch.setattr(coach, "_transport", httpx.MockTransport(handler))
    return calls, state


client = TestClient(app)


def png_bytes() -> bytes:
    ok, buf = cv2.imencode(".png", np.zeros((300, 200, 3), np.uint8))
    return buf.tobytes()


# --- health -----------------------------------------------------------------

def test_readiness_ok_when_configured():
    r = client.get("/management/health/readiness")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert len(r.json()["prompt_sha256"]) == 64


def test_readiness_503_without_key(monkeypatch):
    monkeypatch.delenv("FOUNDRY_API_KEY")
    r = client.get("/management/health/readiness")
    assert r.status_code == 503
    assert "FOUNDRY_API_KEY is not set." in r.json()["problems"]


def test_index_page_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "Golf Swing Coach" in r.text


# --- analyze ----------------------------------------------------------------

def test_analyze_video(model_reply):
    calls, _ = model_reply
    r = client.post(
        "/api/golf-swing/analyze",
        files={"file": ("swing.mp4", make_video(60), "video/mp4")},
        data={"swing_type": "Iron", "skill_level": "Intermediate", "concerns": "I slice it"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["frames"]) == 6
    assert body["frames"][0]["thumbnail"].startswith("data:image/jpeg;base64,")
    assert body["result"]["scores"]["overall"] == 71
    # out-of-range frame reference is dropped
    assert body["result"]["improvements"][1]["frame"] is None

    sent = json.loads(calls[0].content)
    assert str(calls[0].url) == "https://test-resource.services.ai.azure.com/anthropic/v1/messages"
    assert calls[0].headers["x-api-key"] == "test-key"
    images = [b for b in sent["messages"][0]["content"] if b["type"] == "image"]
    assert len(images) == 6
    assert "I slice it" in sent["messages"][0]["content"][-1]["text"]


def test_analyze_photo(model_reply):
    r = client.post("/api/golf-swing/analyze", files={"file": ("swing.png", png_bytes(), "image/png")})
    assert r.status_code == 200, r.text
    assert len(r.json()["frames"]) == 1
    assert r.json()["frames"][0]["time_s"] is None


def test_analyze_rejects_unknown_club(model_reply):
    r = client.post("/api/golf-swing/analyze",
                    files={"file": ("swing.png", png_bytes(), "image/png")},
                    data={"swing_type": "Bazooka"})
    assert r.status_code == 422


def test_analyze_rejects_unsupported_file(model_reply):
    r = client.post("/api/golf-swing/analyze", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 415


def test_analyze_unreadable_video(model_reply):
    r = client.post("/api/golf-swing/analyze", files={"file": ("swing.mp4", b"junk", "video/mp4")})
    assert r.status_code == 422


def test_model_error_becomes_502(model_reply):
    _, state = model_reply
    state["status"] = 401
    r = client.post("/api/golf-swing/analyze", files={"file": ("swing.png", png_bytes(), "image/png")})
    assert r.status_code == 502
    assert "401" in r.json()["detail"]


def test_model_markdown_fences_are_tolerated(model_reply):
    _, state = model_reply
    state["text"] = "```json\n" + json.dumps(GOOD_REPLY) + "\n```"
    r = client.post("/api/golf-swing/analyze", files={"file": ("swing.png", png_bytes(), "image/png")})
    assert r.status_code == 200


def test_model_bad_json_becomes_502(model_reply):
    _, state = model_reply
    state["text"] = "Sorry, I can't help with that."
    r = client.post("/api/golf-swing/analyze", files={"file": ("swing.png", png_bytes(), "image/png")})
    assert r.status_code == 502
