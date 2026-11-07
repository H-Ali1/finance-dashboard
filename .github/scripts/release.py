"""Release helper for the CI workflow.

    python .github/scripts/release.py plan [--force]
        Decide whether this commit needs a release, and at what version.
        Writes `release=true|false` and `version=X.Y.Z` to $GITHUB_OUTPUT.

    python .github/scripts/release.py assemble --version X.Y.Z --dest DIR
        Fill DIR (a checkout of the `ha-release` branch) with what Home
        Assistant reads: repository.yaml and the App folder, with the version
        set and a changelog entry added.

Versions: the patch number goes up by one on every release. To jump further,
raise `version` in homeassistant/config.yaml (e.g. to 1.2.0) and the next
release uses it. A git tag vX.Y.Z marks the source commit of each release, and
"what changed since the last release" is measured from the newest tag.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "homeassistant"
APP_FOLDER = "finance_dashboard"  # folder name on the ha-release branch

# Changes under these paths ship to Home Assistant; anything else (tests, docs,
# CI config) doesn't need a release.
RELEASE_PATHS = [
    "backend/app",
    "backend/requirements.txt",
    "frontend",
    "Dockerfile",
    ".dockerignore",
    "homeassistant",
    ":(exclude)homeassistant/CHANGELOG.md",
]
VERSION_LINE = re.compile(r'^version:\s*"?(\d+)\.(\d+)\.(\d+)"?\s*$', re.M)
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def config_version() -> tuple[int, int, int]:
    match = VERSION_LINE.search((APP_DIR / "config.yaml").read_text(encoding="utf-8"))
    if not match:
        raise SystemExit("homeassistant/config.yaml has no `version: X.Y.Z` line")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def released_versions() -> set[tuple[int, int, int]]:
    tags = git("tag", "--list", "v*").split()
    return {tuple(int(p) for p in m.groups()) for t in tags if (m := TAG.match(t))}  # type: ignore[misc]


def last_release_tag(commit: str) -> str | None:
    tag = git("describe", "--tags", "--abbrev=0", "--match", "v*", commit, check=False)
    return tag or None


def next_version(base: tuple[int, int, int], released: set[tuple[int, int, int]]) -> tuple[int, int, int]:
    if not released:
        return base
    newest = max(released)
    if base not in released and base > newest:
        return base  # a hand-raised version in config.yaml
    return (newest[0], newest[1], newest[2] + 1)


def fmt(version: tuple[int, int, int]) -> str:
    return ".".join(str(p) for p in version)


def plan(force: bool) -> None:
    commit = os.environ.get("GITHUB_SHA") or git("rev-parse", "HEAD")
    last = last_release_tag(commit)
    if last and not force:
        changed = git("diff", "--name-only", f"{last}..{commit}", "--", *RELEASE_PATHS)
        if not changed:
            print(f"No app changes since {last}: nothing to release.")
            _output(release="false", version="")
            return
        print(f"App changes since {last}:\n{changed}")
    version = fmt(next_version(config_version(), released_versions()))
    print(f"Releasing {version}")
    _output(release="true", version=version)


def _output(**values: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{key}={value}\n" for key, value in values.items())
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(lines)
    else:
        sys.stdout.write(lines)


def release_notes(version: str) -> str:
    commit = os.environ.get("GITHUB_SHA") or git("rev-parse", "HEAD")
    last = last_release_tag(commit)
    span = f"{last}..{commit}" if last else commit
    subjects = git("log", "--no-merges", "--pretty=format:%s", span, "--", *RELEASE_PATHS).splitlines()
    subjects = [s for s in subjects if s and "[skip ci]" not in s][:30]
    body = "\n".join(f"- {s}" for s in subjects) or "- Maintenance release."
    return f"## {version}\n\n{body}\n"


def assemble(version: str, dest: Path) -> None:
    previous = dest / APP_FOLDER / "CHANGELOG.md"
    changelog = (previous if previous.exists() else APP_DIR / "CHANGELOG.md").read_text(encoding="utf-8")

    for child in dest.iterdir():  # start clean, keep the git metadata
        if child.name == ".git":
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()

    shutil.copy2(APP_DIR / "repository.yaml", dest / "repository.yaml")
    shutil.copytree(
        APP_DIR,
        dest / APP_FOLDER,
        ignore=shutil.ignore_patterns("repository.yaml", "CHANGELOG.md"),
    )

    config = dest / APP_FOLDER / "config.yaml"
    text, count = VERSION_LINE.subn(f'version: "{version}"', config.read_text(encoding="utf-8"), count=1)
    if count != 1:
        raise SystemExit("couldn't set the version in config.yaml")
    config.write_text(text, encoding="utf-8")

    if f"## {version}\n" not in changelog:
        header, _, rest = changelog.partition("\n## ")
        changelog = f"{header.rstrip()}\n\n{release_notes(version)}" + (f"\n## {rest}" if rest else "")
    (dest / APP_FOLDER / "CHANGELOG.md").write_text(changelog, encoding="utf-8")

    (dest / "README.md").write_text(
        "# Finance Dashboard: Home Assistant App repository\n\n"
        "Generated by CI from the `main` branch. Don't edit it here.\n\n"
        "In Home Assistant: **Settings → Apps → App store → ⋮ → Repositories**, add\n\n"
        "    https://github.com/H-Ali1/finance-dashboard#ha-release\n",
        encoding="utf-8",
    )
    print(f"Assembled release {version} in {dest}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--force", action="store_true")
    p_asm = sub.add_parser("assemble")
    p_asm.add_argument("--version", required=True)
    p_asm.add_argument("--dest", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "plan":
        plan(args.force)
    else:
        assemble(args.version, args.dest)


if __name__ == "__main__":
    main()
