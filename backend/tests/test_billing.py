"""The business model: plans and their limits (app/plans.py), offline license keys
(app/licensing.py) and usage metering (app/usage.py)."""

import base64
import dataclasses
import json
from datetime import date, timedelta

import httpx
import pytest

from app import cli, embeddings, licensing, memory, orchestrator, plans, scheduler, tasks, usage
from app.config import ConfigError, load_settings, reset_settings
from app.model_backends import ModelResponse, set_backend
from app.model_backends.base import ModelBackend
from app.model_backends.nebius_nemotron_backend import NebiusNemotronBackend

SIGNING_KEY = licensing.new_signing_key()
PUBLIC_KEY = licensing.public_key_text(SIGNING_KEY)


@pytest.fixture
def vendor(monkeypatch):
    """This build trusts SIGNING_KEY, as a release build trusts the vendor's real key."""
    monkeypatch.setattr(licensing, "VENDOR_PUBLIC_KEYS", (PUBLIC_KEY,))
    plans.reset()


def use_plan(monkeypatch, plan_id: str, **limits) -> None:
    """Preview a plan (development), optionally with smaller limits for the test."""
    monkeypatch.setenv("CUSTODIAN_PLAN", plan_id)
    if limits:
        monkeypatch.setitem(plans.PLANS, plan_id, dataclasses.replace(plans.PLANS[plan_id], **limits))
    reset_settings()
    plans.reset()


def production(monkeypatch, license_key: str | None = None) -> None:
    monkeypatch.setenv("CUSTODIAN_ENV", "production")
    if license_key:
        monkeypatch.setenv("CUSTODIAN_LICENSE_KEY", license_key)
    reset_settings()
    plans.reset()


def pro_key(**kwargs) -> str:
    return licensing.issue(SIGNING_KEY, "pro", "Om Choudhari", days=kwargs.pop("days", 365), **kwargs)


# License keys ----------------------------------------------------------------------------------

def test_a_license_key_round_trip(vendor):
    lic = licensing.parse(pro_key())
    assert (lic.plan, lic.licensee, lic.status()) == ("pro", "Om Choudhari", "active")
    assert lic.expires == date.today() + timedelta(days=365)
    key = pro_key()
    assert licensing.parse(f"  {key[:40]}\n  {key[40:]}\n").id == licensing.parse(key).id  # a key pasted from an email may wrap


def test_tampered_and_foreign_keys_are_rejected(vendor, monkeypatch):
    prefix, payload, signature = pro_key().split(".")
    data = json.loads(base64.urlsafe_b64decode(payload + "=="))
    data["plan"] = "cloud"
    forged = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    with pytest.raises(licensing.LicenseError, match="signature"):
        licensing.parse(f"{prefix}.{forged}.{signature}")
    with pytest.raises(licensing.LicenseError, match="signature"):
        licensing.parse(licensing.issue(licensing.new_signing_key(), "pro", "Mallory", days=365))
    with pytest.raises(licensing.LicenseError, match="isn't a Custodian license"):
        licensing.parse("not-a-key")
    monkeypatch.setattr(licensing, "VENDOR_PUBLIC_KEYS", ())
    with pytest.raises(licensing.LicenseError, match="no vendor key"):
        licensing.parse(pro_key())


def test_expiry_and_grace(vendor):
    expired = licensing.parse(pro_key(today=date.today() - timedelta(days=400)))
    assert expired.status() == "expired"
    grace = licensing.parse(pro_key(today=date.today() - timedelta(days=368)))
    assert grace.status() == "grace"
    perpetual = licensing.parse(licensing.issue(SIGNING_KEY, "pro", "Om", days=None))
    assert perpetual.expires is None and perpetual.status() == "active"


# Which plan applies ----------------------------------------------------------------------------

def test_development_previews_pro_unless_told_otherwise(monkeypatch):
    state = plans.current()
    assert (state.plan.id, state.source) == ("pro", "preview")
    use_plan(monkeypatch, "free")
    assert plans.current().plan.id == "free"


def test_production_without_a_license_is_free(monkeypatch):
    production(monkeypatch)
    assert (plans.current().plan.id, plans.current().source) == ("free", "free")


def test_a_license_unlocks_its_plan_and_an_expired_one_does_not(monkeypatch, vendor):
    production(monkeypatch, pro_key())
    state = plans.current()
    assert (state.plan.id, state.source, state.license.licensee) == ("pro", "license", "Om Choudhari")

    production(monkeypatch, pro_key(today=date.today() - timedelta(days=400)))
    state = plans.current()
    assert state.plan.id == "free" and "expired" in state.problem


def test_activating_a_license_in_the_app(client, monkeypatch, vendor):
    production(monkeypatch)
    assert client.get("/billing").json()["plan"]["id"] == "free"
    bad = client.post("/billing/license", json={"key": "CL1.garbage.garbage"})
    assert bad.status_code == 400
    billing = client.post("/billing/license", json={"key": pro_key()}).json()
    assert billing["plan"]["id"] == "pro" and billing["source"] == "license"
    assert billing["license"]["licensee"] == "Om Choudhari" and billing["license"]["status"] == "active"
    assert client.delete("/billing/license").json()["plan"]["id"] == "free"


def test_plan_settings_are_checked(monkeypatch):
    monkeypatch.setenv("CUSTODIAN_PLAN", "gold")
    with pytest.raises(ConfigError, match="CUSTODIAN_PLAN must be one of"):
        load_settings()
    monkeypatch.setenv("CUSTODIAN_PLAN", "pro")
    monkeypatch.setenv("CUSTODIAN_ENV", "production")
    with pytest.raises(ConfigError, match="only previews a plan in development"):
        load_settings()


# Limits ----------------------------------------------------------------------------------------

def test_the_free_plan_memory_limit(user_id, client, monkeypatch):
    use_plan(monkeypatch, "free", max_memories=2)
    assert client.post("/memories", json={"content": "Likes jazz."}).status_code == 201
    assert client.post("/memories", json={"content": "Likes tea."}).status_code == 201
    full = client.post("/memories", json={"content": "Likes rain."})
    assert full.status_code == 402 and "keeps up to 2 memories" in full.json()["detail"]

    out = orchestrator.handle(user_id, "Remember that I prefer window seats")  # the model's save is refused too
    assert out["used"]["remembered"] == [] and len(memory.list_memories(user_id)) == 2
    assert out["used"]["checks"]["status"] == "passed"  # and the reply doesn't pretend otherwise


def test_the_free_plan_runs_one_scheduled_task(user_id, client, monkeypatch):
    use_plan(monkeypatch, "free")
    first = client.post("/tasks", json={"name": "morning", "skill": "general", "cron_schedule": "0 8 * * *"})
    assert first.status_code == 201
    second = client.post("/tasks", json={"name": "evening", "skill": "general", "cron_schedule": "0 20 * * *"})
    assert second.status_code == 402 and "runs 1 scheduled task" in second.json()["detail"]
    paused = client.post("/tasks", json={"name": "evening", "skill": "general", "cron_schedule": "0 20 * * *", "status": "paused"})
    assert paused.status_code == 201
    assert client.patch(f"/tasks/{paused.json()['id']}", json={"status": "active"}).status_code == 402
    assert client.post(f"/tasks/{paused.json()['id']}/run").json()["status"] == "succeeded"  # "Run now" is fine

    # After a downgrade, the oldest active task keeps running and the rest are skipped, not deleted.
    use_plan(monkeypatch, "pro")
    client.patch(f"/tasks/{paused.json()['id']}", json={"status": "active"})
    use_plan(monkeypatch, "free")
    skipped = tasks.get_run(user_id, scheduler.run_task(paused.json()["id"]))
    assert skipped["status"] == "failed" and skipped["error"].startswith("skipped: the Free plan runs 1 scheduled task")
    assert tasks.get_run(user_id, scheduler.run_task(first.json()["id"]))["status"] == "succeeded"


def test_search_by_meaning_is_a_pro_feature(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:11434/v1")
    use_plan(monkeypatch, "free")
    assert embeddings.get_embedder() is None
    use_plan(monkeypatch, "pro")
    assert embeddings.get_embedder() is not None


def test_the_cloud_plan_model_allowance(user_id, client, monkeypatch):
    use_plan(monkeypatch, "cloud", monthly_tokens=50)
    assert client.post("/chat", json={"message": "hello"}).status_code == 200  # uses well over 50 tokens
    blocked = client.post("/chat", json={"message": "hello again"})
    assert blocked.status_code == 402 and "allowance" in blocked.json()["detail"]
    task = tasks.create_task(user_id, "daily", "general", "0 9 * * *")
    run = tasks.get_run(user_id, scheduler.run_task(task["id"]))
    assert run["error"].startswith("skipped:") and "allowance" in run["error"]


# Usage -----------------------------------------------------------------------------------------

def test_usage_is_metered(client, monkeypatch):
    client.post("/chat", json={"message": "hello"})
    client.post("/chat", json={"message": "What's on my calendar today?"})
    billing = client.get("/billing").json()
    today = billing["usage"]["today"]
    assert today["chat_turns"] == 2 and today["model_calls"] >= 2 and today["tokens"] > 0
    assert billing["usage"]["estimated"] is True  # the offline model reports no token counts
    assert today["cost_usd"] is None and billing["usage"]["prices_set"] is False  # no price, no made-up cost

    monkeypatch.setenv("MODEL_PRICE_INPUT_PER_M", "0.30")
    monkeypatch.setenv("MODEL_PRICE_OUTPUT_PER_M", "1.20")
    reset_settings()
    month = client.get("/billing").json()["usage"]["month"]
    expected = month["prompt_tokens"] / 1e6 * 0.30 + month["completion_tokens"] / 1e6 * 1.20
    assert month["cost_usd"] == round(expected, 4)


class Counted(ModelBackend):
    name = "counted"
    model_id = "counted-1"

    def generate(self, messages, tools=None):
        return ModelResponse("OK.", [], self.model_id, prompt_tokens=100, completion_tokens=20)


def test_reported_token_counts_are_used(user_id):
    set_backend(Counted())
    orchestrator.handle(user_id, "hi")
    month = usage.summary(user_id)["month"]
    assert (month["prompt_tokens"], month["completion_tokens"], month["estimated_calls"]) == (100, 20, 0)


def test_the_nebius_backend_reads_token_counts():
    def handler(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Hi"}}], "usage": {"prompt_tokens": 12, "completion_tokens": 3}},
        )

    backend = NebiusNemotronBackend("k", "m", "https://api.example.test/v1", transport=httpx.MockTransport(handler))
    out = backend.generate([{"role": "user", "content": "hi"}])
    assert (out.prompt_tokens, out.completion_tokens) == (12, 3)


# Vendor tools ----------------------------------------------------------------------------------

def test_license_commands(tmp_path, capsys):
    key_file = tmp_path / "signing.pem"
    assert cli.main(["license-keygen", "--out", str(key_file)]) == 0
    public = capsys.readouterr().out.split('"')[-2]
    assert cli.main(["license-keygen", "--out", str(key_file)]) == 1  # never overwrites a signing key

    assert cli.main(["license-issue", "--signing-key", str(key_file), "--plan", "pro", "--to", "Om", "--days", "30"]) == 0
    key = capsys.readouterr().out.strip()
    lic = licensing.parse(key, public_keys=(public,))
    assert (lic.plan, lic.licensee, lic.expires) == ("pro", "Om", date.today() + timedelta(days=30))
    assert cli.main(["license-issue", "--signing-key", str(key_file), "--plan", "free", "--to", "Om"]) == 2


def test_export_command(user_id, tmp_path):
    memory.write(user_id, "Prefers green tea.", "preference")
    out = tmp_path / "export.json"
    assert cli.main(["export", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["memories"][0]["content"] == "Prefers green tea."
