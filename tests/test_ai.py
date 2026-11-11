"""AI integration: built-in fallback, pluggable providers, provider request formats,
validation of AI answers, key safety and endpoint safety. No real API keys needed -
HTTP is mocked with httpx2.MockTransport."""

import base64
import io
import json
from dataclasses import replace

import cv2
import httpx2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from embroidery_app import service
from embroidery_app.ai import base, registry
from embroidery_app.ai.base import EDIT, VISION, AIError, Credentials, ModelInfo, Provider
from embroidery_app.ai.registry import register
from embroidery_app.api import app

client = TestClient(app)
KEY = "sk-test-SECRETKEY1234567890"


def _png(color=(30, 60, 200), size=(80, 120)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _project() -> str:
    img = np.full((400, 600, 3), 255, np.uint8)
    cv2.rectangle(img, (60, 60), (300, 340), (40, 40, 200), -1)
    cv2.circle(img, (450, 200), 100, (20, 140, 20), -1)
    pid = client.post("/api/projects", files={"file": ("b.png", cv2.imencode(".png", img)[1].tobytes())}).json()["id"]
    assert client.post(f"/api/projects/{pid}/extract", json={"options": {"width_mm": 80}}).status_code == 200
    return pid


# --------------------------------------------------------------------------- a pluggable test provider

class FakeProvider(Provider):
    id = "fake"
    label = "Fake AI"
    capabilities = (EDIT, VISION)
    default_base_url = "https://fake.example"
    env_prefix = "FAKE_AI"
    answers: dict = {}
    fail = False
    seen: list = []

    def list_models(self, creds):
        return [ModelInfo("fake-vision-1", capabilities=(VISION,)), ModelInfo("fake-vision-2", capabilities=(VISION,)),
                ModelInfo("fake-image-1", capabilities=(EDIT,))]

    def rank(self, capability, model_id):
        return 1.0 if model_id.endswith("2") else 0.0

    def vision_json(self, creds, model, images, prompt):
        FakeProvider.seen.append({"model": model, "images": len(images), "key": creds.api_key, "prompt": prompt})
        if FakeProvider.fail:
            raise AIError("simulated outage")
        return FakeProvider.answers.get("vision", {})

    def edit_image(self, creds, model, png, prompt):
        FakeProvider.seen.append({"model": model, "prompt": prompt})
        if FakeProvider.fail:
            raise AIError("simulated outage")
        return _png()


@pytest.fixture
def fake():
    register(FakeProvider)  # adding a provider = one class + @register
    FakeProvider.answers, FakeProvider.fail, FakeProvider.seen = {}, False, []
    registry._cache.clear()
    yield FakeProvider
    registry.PROVIDERS.pop("fake", None)


AI = {"provider": "fake", "api_key": KEY}


# --------------------------------------------------------------------------- built-in fallback (no AI)

def test_suggest_and_review_work_without_any_ai():
    pid = _project()
    s = client.post(f"/api/projects/{pid}/ai/suggest", json={}).json()
    assert s["source"] == "builtin" and s["design_type"] == "logo"
    assert s["extract"]["background"] == "auto" and 1 <= s["extract"]["num_colors"] <= 4
    assert s["measurements"]["thinnest_line_mm"] > 5
    client.post(f"/api/projects/{pid}/stitch", json={"params": {"underlay": False}})
    r = client.post(f"/api/projects/{pid}/ai/review", json={"ai": {"provider": "builtin"}}).json()
    assert r["source"] == "builtin" and r["overall"] == "needs_work"
    assert any("Foundation" in i["title"] for i in r["issues"]) and r["changes"]["underlay"] is True
    # old endpoint name still works
    assert client.post(f"/api/projects/{pid}/ai/analyze", json={}).json()["source"] == "builtin"


def test_cleanup_without_ai_explains_instead_of_failing_silently():
    pid = _project()
    r = client.post(f"/api/projects/{pid}/ai/clean", json={"mode": "redraw"})
    assert r.status_code == 502 and "needs an AI service" in r.json()["detail"]


def test_builtin_flags_thin_lines_and_photos():
    from embroidery_app.ai.builtin import analyze_image

    img = np.full((300, 600, 3), 255, np.uint8)
    cv2.line(img, (20, 150), (580, 150), (0, 0, 0), 2)  # a 2 px line on a 600 px wide picture
    out = analyze_image(cv2.cvtColor(img, cv2.COLOR_BGR2RGBA), width_mm=60)
    assert out["measurements"]["thinnest_line_mm"] < 0.5
    assert any("design width of about" in n for n in out["notes"])
    rng = np.random.default_rng(1)
    yy, xx = np.mgrid[0:300, 0:300]
    photo = np.dstack([xx * 0.8, yy * 0.8, (xx + yy) * 0.4]).astype(np.uint8)
    photo = cv2.add(photo, rng.normal(0, 4, photo.shape).astype(np.int8), dtype=cv2.CV_8U)
    out = analyze_image(cv2.cvtColor(photo, cv2.COLOR_BGR2RGBA), width_mm=100)
    assert out["design_type"] == "photo" and out["extract"]["num_colors"] <= 8


# --------------------------------------------------------------------------- AI on top, validated

def test_ai_suggestion_is_merged_and_validated(fake):
    fake.answers["vision"] = {
        "design_type": "text", "thread_colors": 3, "background_is_part_of_design": True,
        "detail_level": "high", "has_small_text": True, "picture_quality": "noisy_or_blurry",
        "recommended_cleanup": "enhance", "notes": ["Use dense satin for the letters."],
    }
    pid = _project()
    s = client.post(f"/api/projects/{pid}/ai/suggest", json={"ai": AI}).json()
    assert s["source"] == "ai" and s["provider"] == "Fake AI" and s["model"] == "fake-vision-2"  # recommended pick
    assert s["extract"] == {"num_colors": 3, "background": "none", "min_feature_mm": 0.25, "denoise": 3}
    assert s["cleanup"] == "enhance" and s["notes"][0] == "Use dense satin for the letters."
    assert fake.seen[-1]["key"] == KEY

    fake.answers["vision"] = {"thread_colors": 99, "detail_level": "extreme", "recommended_cleanup": "rm -rf", "notes": "x"}
    s = client.post(f"/api/projects/{pid}/ai/suggest", json={"ai": AI}).json()
    assert 1 <= s["extract"]["num_colors"] <= 15 and s["cleanup"] in ("none", "redraw", "remove_background", "enhance")


def test_ai_review_changes_are_clamped_and_unknown_ignored(fake):
    fake.answers["vision"] = {
        "overall": "needs_work",
        "issues": [{"severity": "critical!", "title": "Gaps at the edges", "detail": "Fabric shows."}, "junk"],
        "changes": {"fill_spacing_mm": 5, "pull_compensation_mm": 0.35, "delete_everything": True, "underlay": "yes"},
    }
    pid = _project()
    client.post(f"/api/projects/{pid}/stitch", json={"params": {}})
    r = client.post(f"/api/projects/{pid}/ai/review", json={"ai": {**AI, "vision_model": "fake-vision-1"}}).json()
    assert r["source"] == "ai" and r["model"] == "fake-vision-1" and r["overall"] == "needs_work"
    gap = next(i for i in r["issues"] if i["title"] == "Gaps at the edges")
    assert gap["severity"] == "low" and gap["by_ai"]
    assert r["changes"]["fill_spacing_mm"] == 1.0 and r["changes"]["pull_compensation_mm"] == 0.35
    assert "delete_everything" not in r["changes"] and "underlay" not in r["changes"]
    assert fake.seen[-1]["images"] == 2  # stitch preview + original artwork


def test_ai_failure_falls_back_to_builtin(fake):
    fake.fail = True
    pid = _project()
    s = client.post(f"/api/projects/{pid}/ai/suggest", json={"ai": AI}).json()
    assert s["source"] == "builtin" and "simulated outage" in s["ai_error"] and "built-in" in s["ai_error"]
    client.post(f"/api/projects/{pid}/stitch", json={"params": {}})
    r = client.post(f"/api/projects/{pid}/ai/review", json={"ai": AI}).json()
    assert r["source"] == "builtin" and "ai_error" in r


def test_ai_cleanup_modes_revert_and_key_never_stored(fake):
    pid = _project()
    for mode in ("redraw", "remove_background", "enhance"):
        meta = client.post(f"/api/projects/{pid}/ai/clean", json={"ai": AI, "mode": mode, "colors": 4}).json()
        assert meta["ai_cleaned"] == {"provider": "Fake AI", "model": "fake-image-1", "mode": mode}
    assert "at most 4 colours" not in fake.seen[-1]["prompt"]  # enhance prompt has no colour limit
    assert client.post(f"/api/projects/{pid}/ai/clean", json={"ai": AI, "mode": "paint"}).status_code == 400
    assert client.post(f"/api/projects/{pid}/ai/revert").json()["ai_cleaned"] is False
    for f in (p for p in (service.PROJECTS / pid).rglob("*") if p.is_file()):  # incl. undo history
        assert KEY.encode() not in f.read_bytes(), f"API key leaked into {f.name}"


def test_models_endpoint_and_registry_choice(fake):
    r = client.post("/api/ai/models", json={"ai": AI}).json()
    assert {m["id"] for m in r["models"]} == {"fake-vision-1", "fake-vision-2", "fake-image-1"}
    assert r["recommended"] == {"edit": "fake-image-1", "vision": "fake-vision-2"}
    assert client.post("/api/ai/models", json={}).status_code == 400
    assert any(p["id"] == "fake" for p in client.get("/api/config").json()["ai"])


def test_vector_projects_reject_picture_tools():
    pid = client.post("/api/projects", files={"file": ("a.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>')}).json()["id"]
    assert client.post(f"/api/projects/{pid}/ai/suggest", json={}).status_code == 400


# --------------------------------------------------------------------------- provider request formats (mocked HTTP)

@pytest.fixture
def http(monkeypatch):
    calls = []
    responses = []

    def handler(req: httpx2.Request):
        calls.append(req)
        status, body = responses.pop(0) if responses else (200, {})
        return httpx2.Response(status, json=body)

    real = httpx2.Client
    monkeypatch.setattr(base.httpx2, "Client", lambda **kw: real(transport=httpx2.MockTransport(handler), **kw))
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    return calls, responses


def test_openai_vision_request_format(http):
    calls, responses = http
    responses.append((200, {"choices": [{"message": {"content": '{"overall": "good"}'}}]}))
    p = registry.get("openai")
    out = p.vision_json(p.resolve(Credentials(api_key=KEY)), "gpt-x-mini", [_png()], "hi")
    req = calls[0]
    body = json.loads(req.content)
    assert out == {"overall": "good"}
    assert str(req.url) == "https://api.openai.com/v1/chat/completions"
    assert req.headers["Authorization"] == f"Bearer {KEY}" and body["model"] == "gpt-x-mini"
    assert body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_gemini_edit_and_model_listing(http):
    calls, responses = http
    img = base64.b64encode(_png()).decode()
    responses.append((200, {"candidates": [{"content": {"parts": [{"text": "here"}, {"inlineData": {"mimeType": "image/png", "data": img}}]}}]}))
    responses.append((200, {"models": [
        {"name": "models/gemini-3.1-flash-image", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.6-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.7-flash-preview", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-9", "supportedGenerationMethods": ["embedContent"]},
    ]}))
    p = registry.get("gemini")
    creds = p.resolve(Credentials(api_key=KEY))
    assert p.edit_image(creds, "gemini-3.1-flash-image", _png(), "clean") == base64.b64decode(img)
    assert calls[0].headers["x-goog-api-key"] == KEY and "/models/gemini-3.1-flash-image:generateContent" in str(calls[0].url)
    models = p.list_models(creds)
    rec = registry.recommended(p, models)
    assert rec == {"edit": "gemini-3.1-flash-image", "vision": "gemini-3.6-flash"}  # stable beats preview


def test_anthropic_and_openrouter_and_ollama_formats(http):
    calls, responses = http
    responses.append((200, {"content": [{"type": "text", "text": 'Sure! {"overall": "ok"}'}]}))
    p = registry.get("anthropic")
    assert p.vision_json(p.resolve(Credentials(api_key=KEY)), "claude-sonnet-5-5", [_png()], "x") == {"overall": "ok"}
    assert calls[-1].headers["x-api-key"] == KEY and calls[-1].headers["anthropic-version"]
    assert json.loads(calls[-1].content)["messages"][0]["content"][0]["type"] == "image"

    responses.append((200, {"data": [
        {"id": "google/gemini-3.1-flash-image", "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["image", "text"]}},
        {"id": "meta/text-only", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
    ]}))
    p = registry.get("openrouter")
    models = p.list_models(p.resolve(Credentials(api_key=KEY)))
    assert [m.id for m in models] == ["google/gemini-3.1-flash-image"] and set(models[0].capabilities) == {EDIT, VISION}
    assert "output_modalities=all" in str(calls[-1].url)

    responses.extend([
        (200, {"models": [{"name": "qwen3-vl:8b"}, {"name": "qwen3:4b"}]}),
        (200, {"capabilities": ["completion", "vision"]}),
        (200, {"capabilities": ["completion"]}),
    ])
    p = registry.get("ollama")
    assert [m.id for m in p.list_models(p.resolve(Credentials()))] == ["qwen3-vl:8b"]


def test_http_errors_retries_and_key_redaction(http):
    calls, responses = http
    creds = Credentials(api_key=KEY, base_url="https://api.openai.com/v1")
    responses.extend([(503, {"error": {"message": "busy"}}), (200, {"data": []})])
    assert base.request(creds, "GET", "https://api.openai.com/v1/models").status_code == 200 and len(calls) == 2
    responses.append((401, {"error": {"message": f"bad key {KEY}"}}))
    with pytest.raises(AIError, match="key was rejected"):
        base.request(creds, "GET", "https://api.openai.com/v1/models")
    responses.extend([(400, {"error": {"message": f"Invalid key {KEY} for model"}})])
    with pytest.raises(AIError) as err:
        base.request(creds, "GET", "https://api.openai.com/v1/models")
    assert KEY not in str(err.value) and "***" in str(err.value)


def test_missing_key_and_unsafe_endpoints(monkeypatch):
    with pytest.raises(AIError, match="API key"):
        registry.get("openai").resolve(Credentials())
    with pytest.raises(AIError, match="http"):
        base.check_endpoint("file:///etc/passwd")
    monkeypatch.setattr(base, "settings", replace(base.settings, ai_allow_private_endpoints=False))
    for url in ("http://127.0.0.1:11434", "http://localhost:1234/v1", "http://169.254.169.254/latest"):
        with pytest.raises(AIError, match="private or local"):
            registry.get("custom").resolve(Credentials(base_url=url, user_base_url=True))
    r = client.post("/api/ai/models", json={"ai": {"provider": "custom", "base_url": "http://127.0.0.1:9"}})
    assert r.status_code == 502 and "private or local" in r.json()["detail"]
