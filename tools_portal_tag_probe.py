#!/usr/bin/env python3
"""
portal_tag_probe.py

Purpose
-------
Find the newest *xdg-desktop-portal-gnome* git tag that successfully
**configures with Meson on the *current machine*** (no guessing). This avoids
hard‑coded versions and integrates cleanly into build scripts.

Key design choices
------------------
- **Truth from Meson**: actually runs `meson setup` per tag in a temp build dir.
- **Reusable API**: importable functions *and* a CLI (`python -m tools.portal_tag_probe`).
- **Project‑friendly**: can manage the repo under `./sources/xdg-desktop-portal-gnome`
  or use `--repo` to point elsewhere; can also `--checkout` the chosen tag.
- **Machine constraints**: surfaces concise failure reasons (e.g., GTK version).

Typical workflow
----------------
1) Discover the best tag:
    python -m tools.portal_tag_probe --repo ./sources/xdg-desktop-portal-gnome --print-tag

2) Ensure the repo is cloned and checked out to that tag:
    python -m tools.portal_tag_probe --repo ./sources/xdg-desktop-portal-gnome --checkout

3) Then run your patcher/build as before against `./sources/xdg-desktop-portal-gnome`.

"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

# ----------------------------- low-level utils ------------------------------ #

def _run(cmd: List[str], cwd: Optional[str] = None, timeout: int = 420) -> Tuple[int, str]:
    """Run a command; return (exit_code, combined_output)."""
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout


def _ensure_repo(repo_path: str) -> str:
    """Clone the portal repo if missing; otherwise fetch tags. Return path."""
    if os.path.isdir(repo_path):
        _run(["git", "fetch", "--tags", "--quiet"], cwd=repo_path)
        return repo_path
    os.makedirs(os.path.dirname(repo_path) or ".", exist_ok=True)
    code, out = _run([
        "git",
        "clone",
        "https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome.git",
        repo_path,
    ])
    if code != 0:
        raise SystemExit(f"Failed to clone portal repo into {repo_path}:\n{out}")
    return repo_path


def _list_tags(repo_path: str) -> List[str]:
    code, out = _run(["git", "tag", "--list"], cwd=repo_path)
    if code != 0:
        raise SystemExit(out)

    def vkey(tag: str) -> Tuple[int, ...]:
        # numeric-friendly sort: extract digit runs
        nums: List[int] = []
        cur = []
        for ch in tag:
            if ch.isdigit():
                cur.append(ch)
            else:
                if cur:
                    nums.append(int("".join(cur)))
                    cur = []
        if cur:
            nums.append(int("".join(cur)))
        return tuple(nums)

    return sorted(out.splitlines(), key=vkey, reverse=True)


def _meson_try(repo_path: str, tag: str, prefix: str = "/usr") -> Tuple[bool, str]:
    """Checkout tag and run a configure only; return (ok, log)."""
    code, out = _run(["git", "checkout", "--quiet", tag], cwd=repo_path)
    if code != 0:
        return False, f"git checkout {tag} failed:\n{out}"

    builddir = tempfile.mkdtemp(prefix=f"xdpg-{tag.replace('/', '_')}-")
    try:
        cmd = [
            "meson",
            "setup",
            builddir,
            f"--prefix={prefix}",
            "--buildtype=release",
            "--warnlevel=1",
        ]
        code, out = _run(cmd, cwd=repo_path)
        return (code == 0), out
    finally:
        shutil.rmtree(builddir, ignore_errors=True)


def _summarize_failure(log: str) -> str:
    wanted: List[str] = []
    for ln in log.splitlines():
        low = ln.lower()
        if ("gtk" in low or "adwaita" in low) and ("need" in low or "found" in low or "dependency" in low):
            wanted.append(ln.strip())
        if len(wanted) >= 6:
            break
    return "\n".join(wanted)


# ----------------------------- public interface ---------------------------- #

@dataclass
class ProbeResult:
    ok: bool
    tag: Optional[str]
    log: str
    failures: List[Tuple[str, str]]  # [(tag, brief_reason)]


def pick_newest_compatible_tag(
    repo_path: str,
    *,
    prefix: str = "/usr",
    include_failure_reasons: bool = True,
) -> ProbeResult:
    repo = _ensure_repo(repo_path)
    tags = _list_tags(repo)
    failures: List[Tuple[str, str]] = []

    for t in tags:
        ok, log = _meson_try(repo, t, prefix=prefix)
        if ok:
            return ProbeResult(True, t, log, failures)
        if include_failure_reasons:
            failures.append((t, _summarize_failure(log)))
    return ProbeResult(False, None, "", failures)


def checkout_tag(repo_path: str, tag: str) -> None:
    code, out = _run(["git", "checkout", "--quiet", tag], cwd=repo_path)
    if code != 0:
        raise SystemExit(f"Failed to checkout {tag}:\n{out}")


# ---------------------------------- CLI ----------------------------------- #

def _cli(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="portal_tag_probe",
        description=(
            "Discover the newest xdg-desktop-portal-gnome tag that configures with Meson on this machine."
        ),
    )
    ap.add_argument(
        "--repo",
        default="./sources/xdg-desktop-portal-gnome",
        help="Portal repo path to use/clone (default: ./sources/xdg-desktop-portal-gnome)",
    )
    ap.add_argument(
        "--prefix",
        default="/usr",
        help="Installation prefix to test in Meson setup (default: /usr)",
    )
    ap.add_argument(
        "--print-tag",
        action="store_true",
        help="Print only the chosen tag to stdout on success (machine-friendly)",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON with tag, failures, and a short log snippet",
    )
    ap.add_argument(
        "--checkout",
        action="store_true",
        help="After discovery, check out the chosen tag in the repo",
    )
    ap.add_argument(
        "--show-failures",
        action="store_true",
        help="Show brief reasons for failed tags (top 10)",
    )

    args = ap.parse_args(list(argv) if argv is not None else None)

    # tool checks
    for tool in ("git", "meson", "pkg-config"):
        if shutil.which(tool) is None:
            print(f"Missing required tool: {tool}", file=sys.stderr)
            return 127

    result = pick_newest_compatible_tag(args.repo, prefix=args.prefix)

    if result.ok and result.tag:
        if args.checkout:
            _ensure_repo(args.repo)
            checkout_tag(args.repo, result.tag)
        if args.json:
            print(json.dumps({
                "ok": True,
                "tag": result.tag,
                "repo": os.path.abspath(args.repo),
            }))
        elif args.print_tag:
            print(result.tag)
        else:
            print(f"\n✅ Compatible tag: {result.tag}")
            print("Clone & check out exactly that tag:")
            print(
                "  git clone https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome.git && "
                "cd xdg-desktop-portal-gnome && "
                f"git checkout {result.tag} && git describe --tags --exact-match"
            )
        return 0

    # failure case
    if args.json:
        print(json.dumps({
            "ok": False,
            "failures": result.failures,
        }))
    else:
        print("\nNo tag configured successfully with your current toolchain.")
        if args.show_failures and result.failures:
            print("\nRecent failure reasons (newest first):")
            for t, reason in result.failures[:10]:
                print(f"- {t}:\n  {reason or '(see meson output)'}")
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
