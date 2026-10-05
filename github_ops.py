"""
Surface AI Factory — GitHub operations.
Handles cloning, forking, creating repos, and pushing to AI-Runs-Local.
"""

from __future__ import annotations
import os
import re
import time
import requests
from pathlib import Path

try:
    import git as gitpython
    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False
    print("[WARN] gitpython not installed — using subprocess git fallback")

import subprocess


def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def repo_name_from_url(url: str) -> str:
    """Extract repo name from a GitHub URL."""
    m = re.search(r"github\.com[/:][\w-]+/([\w.-]+?)(?:\.git)?$", url)
    return m.group(1) if m else url.rstrip("/").split("/")[-1].replace(".git", "")


def clone_repo(url: str, target_dir: Path) -> Path:
    """Clone a GitHub repo into target_dir. Returns the cloned path."""
    name = repo_name_from_url(url)
    dest = target_dir / name

    if dest.exists():
        print(f"  [SKIP] Already cloned at {dest}")
        return dest

    print(f"  [CLONE] {url} → {dest}")
    if GIT_AVAILABLE:
        gitpython.Repo.clone_from(url, str(dest), depth=1)
    else:
        subprocess.run(["git", "clone", "--depth=1", url, str(dest)], check=True)

    return dest


def get_default_branch(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=str(repo_root), capture_output=True, text=True
        )
        return result.stdout.strip() or "main"
    except Exception:
        return "main"


def ensure_target_repo(token: str, target_org: str, repo_name: str, description: str = "") -> dict:
    """
    Create the target repo in target_org if it doesn't exist.
    Returns the repo info dict from GitHub API.
    """
    headers = _gh_headers(token)
    url = f"https://api.github.com/repos/{target_org}/{repo_name}"
    r = requests.get(url, headers=headers)

    if r.status_code == 200:
        print(f"  [EXISTS] {target_org}/{repo_name} already exists")
        return r.json()

    # Create it
    print(f"  [CREATE] Creating {target_org}/{repo_name} in GitHub org")
    payload = {
        "name": repo_name,
        "description": description or f"Surface AI Factory — {repo_name}",
        "private": False,
        "auto_init": False,
    }
    r = requests.post(
        f"https://api.github.com/orgs/{target_org}/repos",
        headers=headers,
        json=payload,
        timeout=30,
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Failed to create repo: {r.status_code} {r.text}")
    print(f"  [CREATED] https://github.com/{target_org}/{repo_name}")
    time.sleep(2)  # brief pause for GitHub to initialise
    return r.json()


def push_to_target(
    repo_root: Path,
    token: str,
    target_org: str,
    repo_name: str,
    branch: str = "main",
    commit_message: str = "chore: apply Surface AI Factory best practices",
):
    """
    Commit all changes in repo_root and push to the target GitHub org.
    Uses an authenticated HTTPS remote.
    """
    remote_url = f"https://x-access-token:{token}@github.com/{target_org}/{repo_name}.git"

    def _git(*args):
        result = subprocess.run(
            ["git"] + list(args),
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 and result.stderr:
            # filter credential noise
            err = result.stderr.replace(token, "***")
            print(f"  [GIT] {' '.join(args)}: {err.strip()}")
        return result

    # Configure identity for the commit
    _git("config", "user.email", "surface-ai-factory@microsoft.com")
    _git("config", "user.name", "Surface AI Factory Harness")

    # Stage all changes
    _git("add", "-A")

    # Check if there's anything to commit
    status = _git("status", "--porcelain")
    if not status.stdout.strip():
        print("  [GIT] Nothing new to commit.")
    else:
        _git("commit", "-m", commit_message)
        print(f"  [COMMIT] Changes committed: {commit_message}")

    # Set or update remote
    remotes = _git("remote").stdout.strip().split()
    if "aif-target" in remotes:
        _git("remote", "set-url", "aif-target", remote_url)
    else:
        _git("remote", "add", "aif-target", remote_url)

    # Determine current branch
    current_branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or branch

    # Push
    print(f"  [PUSH] → github.com/{target_org}/{repo_name} ({current_branch})")
    result = _git("push", "aif-target", f"{current_branch}:{branch}", "--force")

    if result.returncode == 0:
        print(f"  [OK] Pushed to https://github.com/{target_org}/{repo_name}")
        return f"https://github.com/{target_org}/{repo_name}"
    else:
        err = result.stderr.replace(token, "***")
        raise RuntimeError(f"Push failed: {err}")
