"""Store operations, from the repository root:

    python -m store.cli orders                  every order and its license state
    python -m store.cli tenants                 Cloud tenants and their status
    python -m store.cli render                  rewrite the Cloud compose file and Caddyfile
    python -m store.cli suspend SLUG            stop a Cloud tenant (data kept)
    python -m store.cli resume SLUG             start it again
"""

from __future__ import annotations

import argparse
import json

from . import provision
from .server import connect


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("orders")
    sub.add_parser("tenants")
    sub.add_parser("render")
    for name in ("suspend", "resume"):
        sub.add_parser(name).add_argument("slug")
    args = parser.parse_args()
    settings = provision.Settings.from_env()

    if args.command == "orders":
        with connect() as conn:
            rows = conn.execute(
                "SELECT checkout_session, plan, email, status, license_expires, tenant, created_at FROM orders ORDER BY created_at"
            ).fetchall()
        print(json.dumps([dict(r) for r in rows], indent=2))
    elif args.command == "tenants":
        print(json.dumps(provision.tenants(settings), indent=2))
    elif args.command == "render":
        provision.render(settings)
        print(f"wrote {settings.root / 'docker-compose.yml'} and {settings.root / 'Caddyfile'}")
    elif args.command == "suspend":
        provision.suspend(settings, args.slug)
    elif args.command == "resume":
        provision.update(settings, args.slug, TENANT_STATUS="active")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
