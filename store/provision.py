"""Cloud plan hosting: one private Custodian container per customer.

Custodian is single-user by design - one passcode, one encryption key, one database - so the
Cloud plan doesn't share a server between customers. Each tenant gets its own container, data
volume, secrets and HTTPS subdomain (<slug>.CLOUD_DOMAIN), all generated here:

    PROVISION_DIR/
      tenants/<slug>/.env        the tenant's secrets and Cloud license (mode 600)
      docker-compose.yml         every active tenant + Caddy (rewritten on each change)
      Caddyfile                  one HTTPS site per tenant

Suspending a tenant removes its container but keeps its volume, so resubscribing brings the
data back; delete() is the only thing that removes data. With PROVISION_APPLY=true each change
runs `docker compose up -d --remove-orphans`; otherwise run it yourself.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet

CADDY_IP = "172.29.0.10"  # Caddy's fixed address: the only proxy tenants trust for client IPs


@dataclass(frozen=True)
class Settings:
    root: Path
    domain: str
    image: str
    nebius_api_key: str
    nebius_model_id: str
    nebius_base_url: str
    timezone: str
    apply: bool

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            root=Path(os.getenv("PROVISION_DIR", "cloud")).resolve(),
            domain=os.getenv("CLOUD_DOMAIN", "custodian.example.com"),
            image=os.getenv("CLOUD_IMAGE", "custodian:latest"),
            nebius_api_key=os.getenv("CLOUD_NEBIUS_API_KEY", ""),
            nebius_model_id=os.getenv("CLOUD_NEBIUS_MODEL_ID", "nvidia/nemotron-3-super-120b-a12b"),
            nebius_base_url=os.getenv("CLOUD_NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1"),
            timezone=os.getenv("CLOUD_DEFAULT_TIMEZONE", "UTC"),
            apply=os.getenv("PROVISION_APPLY", "").lower() in ("1", "true", "yes"),
        )


def _tenants(settings: Settings) -> Path:
    return settings.root / "tenants"


def slug_for(email: str, taken: set[str]) -> str:
    """'Om.Choudhari+x@gmail.com' -> 'om-choudhari', with a short suffix if that's taken."""
    base = re.sub(r"[^a-z0-9]+", "-", email.split("@")[0].split("+")[0].lower()).strip("-")[:24] or "user"
    slug = base
    while slug in taken or slug in ("www", "api", "admin", "store"):
        slug = f"{base}-{secrets.token_hex(2)}"
    return slug


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _read_env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)


def create(settings: Settings, email: str, license_key: str) -> dict:
    """A new tenant: its secrets, its Cloud license and a place in the compose file. Returns
    {slug, url, passcode} - the passcode exists only here and in the tenant's .env."""
    tenants = _tenants(settings)
    tenants.mkdir(parents=True, exist_ok=True)
    slug = slug_for(email, {p.name for p in tenants.iterdir()})
    passcode = secrets.token_urlsafe(12)
    (tenants / slug).mkdir()
    _write_env(
        tenants / slug / ".env",
        {
            "CUSTODIAN_ENV": "production",
            "CUSTODIAN_PASSCODE": passcode,
            "CUSTODIAN_USER_EMAIL": email,
            "CUSTODIAN_TIMEZONE": settings.timezone,
            "APP_SECRET_KEY": Fernet.generate_key().decode(),
            "SESSION_SECRET": secrets.token_urlsafe(48),
            "CUSTODIAN_LICENSE_KEY": license_key,
            "MODEL_BACKEND": "nebius",
            "NEBIUS_API_KEY": settings.nebius_api_key,
            "NEBIUS_MODEL_ID": settings.nebius_model_id,
            "NEBIUS_BASE_URL": settings.nebius_base_url,
            "CORS_ORIGINS": "",
            "TENANT_STATUS": "active",
        },
    )
    render(settings)
    return {"slug": slug, "url": f"https://{slug}.{settings.domain}", "passcode": passcode}


def update(settings: Settings, slug: str, **changes: str) -> None:
    path = _tenants(settings) / slug / ".env"
    if not path.exists():
        raise LookupError(f"no tenant {slug!r}")
    values = _read_env(path)
    values.update(changes)
    _write_env(path, values)
    render(settings)


def set_license(settings: Settings, slug: str, license_key: str) -> None:
    """A renewal's new key; the tenant picks it up within a minute of restarting."""
    update(settings, slug, CUSTODIAN_LICENSE_KEY=license_key, TENANT_STATUS="active")


def suspend(settings: Settings, slug: str) -> None:
    """Stop the tenant's container; its data volume stays."""
    update(settings, slug, TENANT_STATUS="suspended")


def delete(settings: Settings, slug: str) -> None:
    """Remove the tenant's files. Its data volume must be removed with docker on purpose:
    `docker volume rm cloud_data-<slug>`."""
    shutil.rmtree(_tenants(settings) / slug)
    render(settings)


def tenants(settings: Settings) -> list[dict]:
    root = _tenants(settings)
    if not root.exists():
        return []
    out = []
    for path in sorted(p for p in root.iterdir() if (p / ".env").exists()):
        env = _read_env(path / ".env")
        out.append({"slug": path.name, "email": env.get("CUSTODIAN_USER_EMAIL"), "status": env.get("TENANT_STATUS"),
                    "url": f"https://{path.name}.{settings.domain}"})
    return out


def render(settings: Settings) -> None:
    """Rewrite docker-compose.yml and Caddyfile for the active tenants, then apply if configured."""
    active = [t for t in tenants(settings) if t["status"] == "active"]
    services: dict[str, dict] = {
        "caddy": {
            "image": "caddy:2",
            "restart": "unless-stopped",
            "ports": ["80:80", "443:443"],
            "volumes": ["./Caddyfile:/etc/caddy/Caddyfile:ro", "caddy-data:/data", "caddy-config:/config"],
            "networks": {"cloud-net": {"ipv4_address": CADDY_IP}},
        }
    }
    volumes: dict[str, dict] = {"caddy-data": {}, "caddy-config": {}}
    for tenant in active:
        name = f"custodian-{tenant['slug']}"
        services[name] = {
            "image": settings.image,
            "restart": "unless-stopped",
            "env_file": f"tenants/{tenant['slug']}/.env",
            "environment": {"DATA_DIR": "/data", "FORWARDED_ALLOW_IPS": CADDY_IP},
            "volumes": [f"data-{tenant['slug']}:/data"],
            "read_only": True,
            "tmpfs": ["/tmp"],
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "mem_limit": "768m",
            "networks": ["cloud-net"],
        }
        volumes[f"data-{tenant['slug']}"] = {}
    for tenant in tenants(settings):  # keep suspended tenants' data volumes declared
        volumes.setdefault(f"data-{tenant['slug']}", {})
    compose = {
        "name": "cloud",
        "services": services,
        "networks": {"cloud-net": {"ipam": {"config": [{"subnet": "172.29.0.0/24"}]}}},
        "volumes": volumes,
    }
    settings.root.mkdir(parents=True, exist_ok=True)
    # JSON is valid YAML, and needs no extra dependency.
    (settings.root / "docker-compose.yml").write_text(
        "# Generated by store/provision.py - edits are overwritten.\n" + json.dumps(compose, indent=2) + "\n",
        encoding="utf-8",
    )
    sites = [
        f"{t['slug']}.{settings.domain} {{\n\tencode zstd gzip\n\theader Strict-Transport-Security \"max-age=31536000\"\n"
        f"\treverse_proxy custodian-{t['slug']}:8000\n}}\n"
        for t in active
    ]
    (settings.root / "Caddyfile").write_text(
        "# Generated by store/provision.py - edits are overwritten.\n" + ("\n".join(sites) or "# no active tenants\n"),
        encoding="utf-8",
    )
    if settings.apply:
        subprocess.run(["docker", "compose", "up", "-d", "--remove-orphans"], cwd=settings.root, check=True)
