"""Custodian store: sells Pro and Cloud through Stripe and delivers what was bought.

    GET  /                      plans and prices, with a Buy button each
    POST /checkout/{plan}       starts a Stripe Checkout subscription and redirects to it
    POST /stripe/webhook        Stripe events (signature-checked, each handled once)
    GET  /order/{session_id}    the buyer's page: their license key, or their Cloud address
                                and passcode; bookmarkable (the id is Stripe's, unguessable)

Pro: the webhook issues a signed license key (app/licensing.py) for a year; each renewal
issues the next one on the same page. Cloud: it also creates the buyer's own Custodian
container (store/provision.py); renewals put the new key into it, and a cancelled
subscription suspends it, keeping the data.

Run it on the vendor's machine, next to the license signing key - never on a customer's
server. Settings come from the environment (store/README.md lists them).

    uvicorn store.server:app --port 8100          (from the repository root)
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import licensing  # noqa: E402 - the license format lives with the app that checks it
from app.plans import PLANS  # noqa: E402

from . import provision  # noqa: E402

log = logging.getLogger("store")

SOLD = {"pro": 366, "cloud": 32}  # plan -> days one payment covers (yearly / monthly); renewals extend it
SIGNATURE_TOLERANCE = 300  # seconds a Stripe signature stays valid
STRIPE_API = "https://api.stripe.com/v1/"

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  checkout_session TEXT PRIMARY KEY,
  plan TEXT NOT NULL,
  email TEXT,
  customer TEXT,
  subscription TEXT UNIQUE,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
  license_key TEXT,
  license_expires TEXT,
  tenant TEXT,
  cloud_passcode TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS stripe_events (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  received_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def setting(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = Path(setting("STORE_DB", "store.db"))
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def signing_key():  # noqa: ANN201 - Ed25519PrivateKey
    path = Path(setting("LICENSE_SIGNING_KEY", "~/.custodian/license-signing-key.pem")).expanduser()
    return licensing.load_signing_key(path.read_bytes())


def issue(plan: str, email: str, days: int) -> tuple[str, str]:
    key = licensing.issue(signing_key(), plan, email, days=days)
    return key, licensing.parse(key).expires.isoformat()


# Stripe -----------------------------------------------------------------------------------

def verify_signature(payload: bytes, header: str, secret: str, now: float | None = None) -> None:
    """Raises ValueError unless `header` (Stripe-Signature) signs `payload` with `secret`."""
    pairs = [item.split("=", 1) for item in header.split(",") if "=" in item]
    signatures = [value for key, value in pairs if key.strip() == "v1"]
    try:
        stamp = int(next(value for key, value in pairs if key.strip() == "t"))
    except (StopIteration, ValueError):
        raise ValueError("no timestamp") from None
    if abs((now or time.time()) - stamp) > SIGNATURE_TOLERANCE:
        raise ValueError("timestamp outside the tolerance")
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in signatures):
        raise ValueError("signature mismatch")


def stripe_post(path: str, form: dict[str, str], transport: httpx.BaseTransport | None = None) -> dict:
    with httpx.Client(base_url=STRIPE_API, auth=(setting("STRIPE_SECRET_KEY"), ""), timeout=20, transport=transport) as client:
        resp = client.post(path, data=form)
    if resp.status_code >= 400:
        raise HTTPException(502, detail=f"Stripe returned {resp.status_code}: {resp.text[:200]}")
    return resp.json()


# What each event does -----------------------------------------------------------------------

def checkout_completed(session: dict) -> None:
    plan = (session.get("metadata") or {}).get("plan")
    if plan not in SOLD:
        log.warning("checkout %s has no known plan; ignored", session.get("id"))
        return
    email = (session.get("customer_details") or {}).get("email") or session.get("customer_email") or "customer"
    key, expires = issue(plan, email, SOLD[plan])
    tenant = None
    if plan == "cloud":
        tenant = provision.create(provision.Settings.from_env(), email, key)
    with connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO orders (checkout_session, plan, email, customer, subscription, license_key,
              license_expires, tenant, cloud_passcode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session["id"], plan, email, session.get("customer"), session.get("subscription"), key, expires,
             tenant and tenant["slug"], tenant and tenant["passcode"]),
        )
    log.info("order %s: %s for %s", session["id"], plan, email)


def invoice_paid(invoice: dict) -> None:
    """A renewal (not the first invoice, which checkout_completed covers): issue the next key."""
    if invoice.get("billing_reason") != "subscription_cycle":
        return
    with connect() as conn:
        order = conn.execute("SELECT * FROM orders WHERE subscription = ?", (invoice.get("subscription"),)).fetchone()
    if order is None:
        log.warning("renewal for unknown subscription %s", invoice.get("subscription"))
        return
    key, expires = issue(order["plan"], order["email"], SOLD[order["plan"]])
    if order["tenant"]:
        provision.set_license(provision.Settings.from_env(), order["tenant"], key)
    with connect() as conn:
        conn.execute(
            "UPDATE orders SET license_key = ?, license_expires = ?, status = 'active', updated_at = datetime('now') "
            "WHERE checkout_session = ?",
            (key, expires, order["checkout_session"]),
        )
    log.info("renewed %s until %s", order["checkout_session"], expires)


def subscription_deleted(subscription: dict) -> None:
    """Cancelled: the key runs out on its own; a Cloud container is suspended, data kept."""
    with connect() as conn:
        order = conn.execute("SELECT * FROM orders WHERE subscription = ?", (subscription.get("id"),)).fetchone()
        if order is None:
            return
        conn.execute(
            "UPDATE orders SET status = 'cancelled', updated_at = datetime('now') WHERE checkout_session = ?",
            (order["checkout_session"],),
        )
    if order["tenant"]:
        provision.suspend(provision.Settings.from_env(), order["tenant"])
    log.info("cancelled %s", order["checkout_session"])


HANDLERS = {
    "checkout.session.completed": checkout_completed,
    "invoice.paid": invoice_paid,
    "customer.subscription.deleted": subscription_deleted,
}


# Pages ------------------------------------------------------------------------------------

STYLE = """
:root{--bg:#eef0f3;--card:#fff;--ink:#171a24;--soft:#565c6e;--accent:#5b4fc4;--line:#ccd1db}
@media (prefers-color-scheme:dark){:root{--bg:#121419;--card:#1b1e27;--ink:#e6e8ef;--soft:#a0a7ba;--accent:#8b80f0;--line:#353b4a}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:32px 16px}h1{margin:0 0 4px}p.soft,li.soft{color:var(--soft)}
.plans{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px;margin-top:24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px}
.price{font-size:20px;font-weight:700}ul{padding-left:18px}
button{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:10px 16px;font:inherit;cursor:pointer;width:100%}
code{display:block;word-break:break-all;background:var(--bg);padding:12px;border-radius:8px;font-size:13px}
"""


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
        f"content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title><style>{STYLE}</style>"
        f"</head><body><main>{body}</main></body></html>",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"},
    )


def create_app(transport: httpx.BaseTransport | None = None) -> FastAPI:
    app = FastAPI(title="Custodian store", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/", response_class=HTMLResponse)
    def plans_page() -> HTMLResponse:
        cards = []
        for plan in PLANS.values():
            buy = (
                f"<form method='post' action='/checkout/{plan.id}'><button>Buy {html.escape(plan.name)}</button></form>"
                if plan.id in SOLD else "<p class='soft'>Free forever - just install it.</p>"
            )
            items = "".join(f"<li>{html.escape(f)}</li>" for f in plan.features)
            cards.append(
                f"<div class='card'><h2>{html.escape(plan.name)}</h2><div class='price'>{html.escape(plan.price)}</div>"
                f"<p class='soft'>{html.escape(plan.summary)}</p><ul>{items}</ul>{buy}</div>"
            )
        return page("Custodian plans", "<h1>Custodian</h1><p class='soft'>A private AI assistant with a lifetime "
                    "memory. Every plan includes every privacy control.</p><div class='plans'>" + "".join(cards) + "</div>")

    @app.post("/checkout/{plan}")
    def checkout(plan: str) -> RedirectResponse:
        price = setting(f"STRIPE_PRICE_{plan.upper()}") if plan in SOLD else ""
        if not price:
            raise HTTPException(404, detail="That plan isn't for sale here.")
        base = setting("STORE_BASE_URL", "http://localhost:8100").rstrip("/")
        session = stripe_post(
            "checkout/sessions",
            {
                "mode": "subscription",
                "line_items[0][price]": price,
                "line_items[0][quantity]": "1",
                "success_url": f"{base}/order/{{CHECKOUT_SESSION_ID}}",
                "cancel_url": f"{base}/",
                "metadata[plan]": plan,
                "subscription_data[metadata][plan]": plan,
                "allow_promotion_codes": "true",
            },
            transport,
        )
        return RedirectResponse(session["url"], status_code=303)

    @app.post("/stripe/webhook")
    async def webhook(request: Request) -> JSONResponse:
        payload = await request.body()
        try:
            verify_signature(payload, request.headers.get("Stripe-Signature", ""), setting("STRIPE_WEBHOOK_SECRET"))
        except ValueError as exc:
            log.warning("rejected a webhook: %s", exc)
            raise HTTPException(400, detail="Bad signature") from None
        event = json.loads(payload)
        with connect() as conn:
            fresh = conn.execute(
                "INSERT OR IGNORE INTO stripe_events (id, type) VALUES (?, ?)", (event["id"], event["type"])
            ).rowcount
        if fresh and event["type"] in HANDLERS:
            try:
                HANDLERS[event["type"]](event["data"]["object"])
            except Exception:
                with connect() as conn:  # let Stripe retry it
                    conn.execute("DELETE FROM stripe_events WHERE id = ?", (event["id"],))
                raise
        return JSONResponse({"received": True})

    @app.get("/order/{session_id}", response_class=HTMLResponse)
    def order_page(session_id: str) -> HTMLResponse:
        with connect() as conn:
            order = conn.execute("SELECT * FROM orders WHERE checkout_session = ?", (session_id,)).fetchone()
            passcode = order and order["cloud_passcode"]
            if passcode:  # shown once; after this it lives only in the customer's own server
                conn.execute("UPDATE orders SET cloud_passcode = NULL WHERE checkout_session = ?", (session_id,))
        if order is None:
            return page("Almost there", "<h1>Payment received</h1><p class='soft'>We're preparing your order - "
                        "refresh this page in a few seconds.</p><script>setTimeout(()=>location.reload(),4000)</script>")
        expires = date.fromisoformat(order["license_expires"]).strftime("%d %b %Y") if order["license_expires"] else ""
        status = "" if order["status"] == "active" else "<p><strong>This subscription is cancelled.</strong></p>"
        if order["plan"] == "cloud":
            url = f"https://{order['tenant']}.{provision.Settings.from_env().domain}"
            secret = (
                f"<p>Your sign-in passcode (shown only this once - save it now):</p><code>{html.escape(passcode)}</code>"
                if passcode else "<p class='soft'>Your passcode was shown when you first opened this page.</p>"
            )
            body = (f"<h1>Your Custodian is ready</h1>{status}<p>Open <a href='{html.escape(url)}'>{html.escape(url)}</a> "
                    f"and sign in.</p>{secret}<p class='soft'>Renews monthly; renewals apply automatically.</p>")
        else:
            body = (
                f"<h1>Your Custodian Pro license</h1>{status}<p>Paste this key into <strong>Account → License key</strong> "
                f"in your Custodian, or set it as <code style='display:inline;padding:2px 6px'>CUSTODIAN_LICENSE_KEY</code> "
                f"on your server:</p><code>{html.escape(order['license_key'])}</code><p class='soft'>Valid until "
                f"{expires}. Bookmark this page: each yearly renewal's new key appears here.</p>"
            )
        return page("Your Custodian order", body)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
