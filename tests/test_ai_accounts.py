"""AI per account: each user's own key and model, a clear message without one, the app's
server keys only with the ``server_ai`` feature, invalid keys and provider failures,
usage records with token counts, and no key anywhere it should not be."""

import json

import httpx2
import pytest

from accounts import admin, new_user, real_login, stitched_project  # noqa: F401
from embroidery_app import audit, db
from embroidery_app.ai import base, registry
from embroidery_app.ai.base import EDIT, VISION, AIError, ModelInfo, Provider
from embroidery_app.ai.registry import register

pytestmark = pytest.mark.usefixtures("real_login")


class Echo(Provider):
    """Remembers which key and model each call used."""

    id = "echo"
    label = "Echo AI"
    capabilities = (EDIT, VISION)
    default_base_url = "https://echo.example"
    env_prefix = "ECHO_AI"
    calls: list = []
    bad_keys: set = set()

    def list_models(self, creds):
        if creds.api_key in Echo.bad_keys:
            raise AIError("The API key was rejected - check it in AI settings.")
        return [ModelInfo("echo-v1", capabilities=(VISION,)), ModelInfo("echo-v2", capabilities=(VISION,))]

    def vision_json(self, creds, model, images, prompt):
        Echo.calls.append((creds.api_key, model))
        if creds.api_key in Echo.bad_keys:
            raise AIError("The API key was rejected - check it in AI settings.")
        return {"thread_colors": 3}


@pytest.fixture(autouse=True)
def echo(monkeypatch):
    register(Echo)
    Echo.calls, Echo.bad_keys = [], set()
    registry._cache.clear()
    monkeypatch.delenv("ECHO_AI_API_KEY", raising=False)
    yield Echo
    registry.PROVIDERS.pop("echo", None)


def setup_ai(c, key=None, model=None):
    entry = {k: v for k, v in (("api_key", key), ("vision_model", model)) if v}
    assert c.put("/api/me/ai", json={"vision": "echo", "providers": {"echo": entry} if entry else {}}).status_code == 200


def suggest(c, pid):
    return c.post(f"/api/projects/{pid}/ai/suggest", json={"ai": {"provider": "echo"}})


def test_each_user_uses_their_own_key_and_model():
    a = admin()
    _, ann = new_user(a)
    _, bob = new_user(a)
    setup_ai(ann, "sk-ann-AAAAAAAAAAAA1111", "echo-v1")
    setup_ai(bob, "sk-bob-BBBBBBBBBBBB2222", "echo-v2")
    pa, pb = stitched_project(ann), stitched_project(bob)
    assert suggest(ann, pa).json()["model"] == "echo-v1"
    assert suggest(bob, pb).json()["model"] == "echo-v2"
    assert Echo.calls == [("sk-ann-AAAAAAAAAAAA1111", "echo-v1"), ("sk-bob-BBBBBBBBBBBB2222", "echo-v2")]
    status = ann.get("/api/me/ai/status").json()
    assert status["advice"] == {"provider": "echo", "label": "Echo AI", "model": "echo-v1", "own_key": True, "ready": True}
    assert "AAAA" not in json.dumps(status)


def test_missing_configuration_gets_a_setup_message_not_someone_elses_key(monkeypatch):
    a = admin()
    _, ann = new_user(a)
    user, cara = new_user(a)
    setup_ai(ann, "sk-ann-AAAAAAAAAAAA1111")
    setup_ai(cara)  # chose the service, never added a key
    monkeypatch.setenv("ECHO_AI_API_KEY", "sk-server-SSSSSSSSSSSS9999")  # the app's own key exists
    pid = stitched_project(cara)
    r = suggest(cara, pid)
    assert r.status_code == 400 and "AI settings" in r.json()["detail"] and Echo.calls == []
    assert cara.get("/api/me/ai/status").json()["advice"]["ready"] is False
    # with the plan feature (here: an individual override) the server key is used
    a.put(f"/api/admin/users/{user['id']}/overrides", json={"features": {"server_ai": True}})
    assert suggest(cara, pid).status_code == 200
    assert Echo.calls == [("sk-server-SSSSSSSSSSSS9999", "echo-v2")]
    assert cara.get("/api/me/ai/status").json()["server_ai"] is True


def test_ai_feature_off_means_built_in_only():
    a = admin()
    user, c = new_user(a)
    setup_ai(c, "sk-zed-ZZZZZZZZZZZZ3333")
    a.put(f"/api/admin/users/{user['id']}/overrides", json={"features": {"ai_services": False}})
    pid = stitched_project(c)
    r = suggest(c, pid)
    assert r.status_code == 403 and r.json()["code"] == "feature_unavailable" and Echo.calls == []
    assert c.post(f"/api/projects/{pid}/ai/suggest", json={}).status_code == 200  # the built-in assistant
    assert c.post("/api/ai/models", json={"ai": {"provider": "echo"}}).status_code == 403


def test_invalid_key_and_provider_failure_are_explained_and_recorded():
    a = admin()
    user, c = new_user(a)
    setup_ai(c, "sk-bad-XXXXXXXXXXXX4444", "echo-v1")
    Echo.bad_keys = {"sk-bad-XXXXXXXXXXXX4444"}
    r = c.post("/api/ai/models", json={"ai": {"provider": "echo"}, "refresh": True})  # "test connection"
    assert r.status_code == 502 and "rejected" in r.json()["detail"]
    pid = stitched_project(c)
    body = suggest(c, pid).json()  # suggestions fall back to the built-in analysis with a note
    assert "rejected" in body["ai_error"] and body.get("source") != "ai"
    with db.tx() as conn:
        rows = [dict(r) for r in conn.execute("SELECT kind, status, provider, model, error FROM usage_records WHERE user_id = ? "
                                              "AND kind LIKE 'ai_%' ORDER BY id", (user["id"],))]
    assert rows == [{"kind": "ai_test", "status": "failed", "provider": "echo", "model": None, "error": "AIError"},
                    {"kind": "ai_suggest", "status": "failed", "provider": "echo", "model": None, "error": "ai_fallback"}]


def test_token_usage_is_recorded_from_provider_answers(monkeypatch):
    def handler(req):
        return httpx2.Response(200, json={"choices": [{"message": {"content": '{"thread_colors": 4}'}}],
                                          "usage": {"prompt_tokens": 321, "completion_tokens": 54}})

    real = httpx2.Client
    monkeypatch.setattr(base.httpx2, "Client", lambda **kw: real(transport=httpx2.MockTransport(handler), **kw))
    a = admin()
    user, c = new_user(a)
    c.put("/api/me/ai", json={"vision": "openai", "providers": {"openai": {"api_key": "sk-tok-TTTTTTTTTTTT5555",
                                                                             "vision_model": "gpt-test"}}})
    pid = stitched_project(c)
    assert c.post(f"/api/projects/{pid}/ai/suggest", json={"ai": {"provider": "openai"}}).json()["model"] == "gpt-test"
    with db.tx() as conn:
        r = conn.execute("SELECT * FROM usage_records WHERE user_id = ? AND kind = 'ai_suggest'", (user["id"],)).fetchone()
    assert (r["provider"], r["model"], r["tokens_in"], r["tokens_out"], r["status"]) == ("openai", "gpt-test", 321, 54, "succeeded")


def test_keys_never_leak_into_records_logs_or_answers(caplog):
    a = admin()
    user, c = new_user(a)
    key = "sk-leak-LLLLLLLLLLLL6666"
    setup_ai(c, key)
    Echo.bad_keys = {key}
    pid = stitched_project(c)
    suggest(c, pid)
    c.post("/api/ai/models", json={"ai": {"provider": "echo", "api_key": key}})
    a.put(f"/api/admin/users/{user['id']}/overrides", json={"features": {"server_ai": True}, "note": "trial"})
    texts = [c.get("/api/me/ai").text, c.get("/api/me/account").text, a.get(f"/api/admin/users/{user['id']}").text,
             a.get("/api/admin/analytics").text, a.get("/api/admin/ai").text, json.dumps(audit.entries(500)), caplog.text]
    with db.tx() as conn:
        for t in ("usage_records", "audit_log", "entitlement_overrides", "subscription_history", "app_settings"):
            texts.append(json.dumps([tuple(r) for r in conn.execute(f"SELECT * FROM {t}")]))
    assert not any(key in t or "LLLLLLLLLLLL" in t for t in texts)
