"""The store end to end, with a throwaway signing key and a mocked Stripe:

    backend/.venv/Scripts/python -m pytest store/tests      (from the repository root)
"""

import hashlib
import hmac
import json
import re
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import licensing
from store import server as store
from store import provision

SECRET = "whsec_test"


@pytest.fixture
def client(tmp_path, monkeypatch):
    key = licensing.new_signing_key()
    key_path = tmp_path / "signing.pem"
    key_path.write_bytes(licensing.signing_key_pem(key))
    monkeypatch.setattr(licensing, "VENDOR_PUBLIC_KEYS", (licensing.public_key_text(key),))
    for name, value in {
        "STORE_DB": str(tmp_path / "store.db"), "LICENSE_SIGNING_KEY": str(key_path),
        "STRIPE_WEBHOOK_SECRET": SECRET, "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_PRICE_PRO": "price_pro",
        "STRIPE_PRICE_CLOUD": "price_cloud", "STORE_BASE_URL": "https://store.test", "PROVISION_DIR": str(tmp_path / "cloud"),
        "CLOUD_DOMAIN": "cloud.test", "CLOUD_NEBIUS_API_KEY": "nb-key", "PROVISION_APPLY": "",
    }.items():
        monkeypatch.setenv(name, value)
    sent = []

    def stripe(request: httpx.Request) -> httpx.Response:
        sent.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={"id": "cs_test_1", "url": "https://checkout.stripe.test/cs_test_1"})

    test_client = TestClient(store.create_app(httpx.MockTransport(stripe)))
    test_client.sent = sent
    return test_client


def signed(event: dict, secret: str = SECRET, at: int | None = None) -> tuple[bytes, dict]:
    payload = json.dumps(event).encode()
    stamp = at or int(time.time())
    signature = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return payload, {"Stripe-Signature": f"t={stamp},v1={signature}", "Content-Type": "application/json"}


def send(client, event_id: str, kind: str, obj: dict):
    payload, headers = signed({"id": event_id, "type": kind, "data": {"object": obj}})
    return client.post("/stripe/webhook", content=payload, headers=headers)


def completed(plan: str, session: str = "cs_1", email: str = "om@example.com") -> dict:
    return {"id": session, "metadata": {"plan": plan}, "customer_details": {"email": email},
            "customer": "cus_1", "subscription": f"sub_{session}"}


def test_signatures():
    payload, headers = signed({"id": "evt"})
    store.verify_signature(payload, headers["Stripe-Signature"], SECRET)
    for bad_payload, bad_header in [
        (payload + b" ", headers["Stripe-Signature"]),
        (payload, signed({"id": "evt"}, secret="other")[1]["Stripe-Signature"]),
        (payload, signed({"id": "evt"}, at=int(time.time()) - 3600)[1]["Stripe-Signature"]),
        (payload, "v1=abc"),
    ]:
        with pytest.raises(ValueError):
            store.verify_signature(bad_payload, bad_header, SECRET)


def test_plans_page_and_checkout(client):
    page = client.get("/").text
    assert "Buy Pro" in page and "Buy Cloud" in page and "Free forever" in page
    resp = client.post("/checkout/pro", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "https://checkout.stripe.test/cs_test_1"
    form = client.sent[-1]
    assert form["line_items[0][price]"] == "price_pro" and form["metadata[plan]"] == "pro"
    assert form["success_url"] == "https://store.test/order/{CHECKOUT_SESSION_ID}"
    assert client.post("/checkout/free").status_code == 404


def test_unsigned_webhooks_are_refused(client):
    payload, _ = signed({"id": "evt_x", "type": "checkout.session.completed", "data": {"object": completed("pro")}})
    assert client.post("/stripe/webhook", content=payload, headers={"Stripe-Signature": "t=1,v1=0"}).status_code == 400
    assert "preparing your order" in client.get("/order/cs_1").text


def test_pro_purchase_delivers_a_working_key(client):
    assert send(client, "evt_1", "checkout.session.completed", completed("pro")).status_code == 200
    assert send(client, "evt_1", "checkout.session.completed", completed("pro")).status_code == 200  # a retry: once only
    page = client.get("/order/cs_1").text
    key = re.search(r"CL1\.[\w.-]+", page).group(0)
    lic = licensing.parse(key)
    assert (lic.plan, lic.licensee, lic.status()) == ("pro", "om@example.com", "active")
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1


def test_cloud_purchase_renewal_and_cancellation(client, tmp_path):
    send(client, "evt_c", "checkout.session.completed", completed("cloud", "cs_c", "Kavya.Nair+cloud@example.com"))
    settings = provision.Settings.from_env()
    [tenant] = provision.tenants(settings)
    assert tenant["slug"] == "kavya-nair" and tenant["status"] == "active"
    env = dict(l.split("=", 1) for l in (Path(settings.root) / "tenants/kavya-nair/.env").read_text().splitlines())
    assert licensing.parse(env["CUSTODIAN_LICENSE_KEY"]).plan == "cloud"
    assert env["CUSTODIAN_ENV"] == "production" and env["NEBIUS_API_KEY"] == "nb-key" and len(env["CUSTODIAN_PASSCODE"]) >= 10
    compose = json.loads((settings.root / "docker-compose.yml").read_text().split("\n", 1)[1])
    assert "custodian-kavya-nair" in compose["services"] and compose["services"]["custodian-kavya-nair"]["read_only"]
    assert "kavya-nair.cloud.test" in (settings.root / "Caddyfile").read_text()

    first = client.get("/order/cs_c").text
    assert env["CUSTODIAN_PASSCODE"] in first and "https://kavya-nair.cloud.test" in first
    assert env["CUSTODIAN_PASSCODE"] not in client.get("/order/cs_c").text  # shown once

    old_key = env["CUSTODIAN_LICENSE_KEY"]
    send(client, "evt_r", "invoice.paid", {"subscription": "sub_cs_c", "billing_reason": "subscription_cycle"})
    env = dict(l.split("=", 1) for l in (settings.root / "tenants/kavya-nair/.env").read_text().splitlines())
    assert env["CUSTODIAN_LICENSE_KEY"] != old_key and licensing.parse(env["CUSTODIAN_LICENSE_KEY"]).plan == "cloud"

    send(client, "evt_d", "customer.subscription.deleted", {"id": "sub_cs_c"})
    assert provision.tenants(settings)[0]["status"] == "suspended"
    compose = json.loads((settings.root / "docker-compose.yml").read_text().split("\n", 1)[1])
    assert "custodian-kavya-nair" not in compose["services"] and "data-kavya-nair" in compose["volumes"]  # data kept
    assert "cancelled" in client.get("/order/cs_c").text


def test_slugs_are_unique_and_safe():
    assert provision.slug_for("Om.Choudhari+x@gmail.com", set()) == "om-choudhari"
    assert provision.slug_for("om.choudhari@x.com", {"om-choudhari"}).startswith("om-choudhari-")
    assert provision.slug_for("admin@x.com", set()).startswith("admin-")
    assert provision.slug_for("../../etc@x.com", set()) == "etc"
