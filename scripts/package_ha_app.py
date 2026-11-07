"""Assemble a local copy of the Home Assistant App, built on the device.

    python scripts/package_ha_app.py

The normal route is the GitHub repository: Home Assistant installs the App from
it and pulls images that CI has already built and tested. This script is for
trying unpublished changes on the device itself.

Writes build/ha-app/finance_dashboard/: the App definition from homeassistant/
(minus its `image:` line, so HA builds it), the Dockerfile, and just the source
files the image needs. No node_modules, virtualenv, database or .env. Copy that
folder into /addons on the Home Assistant machine.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "ha-app" / "finance_dashboard"

FILES = [
    "Dockerfile",
    ".dockerignore",
    "backend/requirements.txt",
    "frontend/index.html",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/tsconfig.json",
    "frontend/vite.config.ts",
]
TREES = ["backend/app", "frontend/src"]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".env", "*.db", "*.db-*", "repository.yaml")


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    # The App definition sits at the top of the folder.
    shutil.copytree(ROOT / "homeassistant", OUT, dirs_exist_ok=True, ignore=IGNORE)
    # Drop `image:` so Home Assistant builds this copy from source instead of
    # pulling the published image from GitHub.
    config = OUT / "config.yaml"
    config.write_text(
        "".join(line for line in config.read_text(encoding="utf-8").splitlines(keepends=True)
                if not line.startswith("image:")),
        encoding="utf-8",
    )

    for rel in FILES:
        target = OUT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    for rel in TREES:
        shutil.copytree(ROOT / rel, OUT / rel, ignore=IGNORE)

    version = re.search(r'^version:\s*"?([^"\n]+)"?', (OUT / "config.yaml").read_text(), re.M)
    files = sum(1 for p in OUT.rglob("*") if p.is_file())
    print(f"Built {OUT.relative_to(ROOT)} (version {version.group(1) if version else '?'}, {files} files)")
    print()
    print("Next: copy the folder to /addons on Home Assistant, for example")
    print(f'  scp -r "{OUT}" root@homeassistant.local:/addons/')
    print("then Settings > Apps > App store > menu > Check for updates, and install")
    print('"Finance Dashboard" from Local apps.')
    return 0


if __name__ == "__main__":
    sys.exit(main())
