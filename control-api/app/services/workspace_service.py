from __future__ import annotations

import base64
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from app.services.naming import workspace_path

logger = logging.getLogger(__name__)


class WorkspaceError(Exception):
    pass


def _redact(text: str, secrets: list[str]) -> str:
    out = text or ""
    for secret in secrets:
        if secret:
            out = out.replace(secret, "***")
    # Also redact basic-auth style tokens in URLs
    out = re.sub(r"(https?://)[^:@\s]+:[^@\s]+@", r"\1***:***@", out)
    out = re.sub(r"(AUTHORIZATION:\s*basic\s+)[A-Za-z0-9+/=]+", r"\1***", out, flags=re.I)
    return out


def prepare_workspace(project_id: int, build_number: int) -> Path:
    root = Path(workspace_path(project_id, build_number))
    if root.exists():
        shutil.rmtree(root)
    (root / "repo").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    return root


def append_log(workspace: Path, line: str, secrets: list[str] | None = None) -> None:
    log_file = workspace / "logs" / "build.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    safe = _redact(line.rstrip("\n"), secrets or [])
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(safe + "\n")


def read_build_log(workspace: Path | str | None, max_bytes: int = 512_000) -> str:
    if not workspace:
        return ""
    path = Path(workspace) / "logs" / "build.log"
    if not path.exists():
        return ""
    data = path.read_bytes()
    if len(data) > max_bytes:
        data = data[-max_bytes:]
    return data.decode("utf-8", errors="replace")


def clone_and_checkout(
    workspace: Path,
    full_name: str,
    commit_sha: str,
    github_token: str | None,
) -> str:
    """Clone repo into workspace/repo and checkout exact SHA. Returns HEAD sha."""
    repo_dir = workspace / "repo"
    secrets = [github_token] if github_token else []
    append_log(workspace, f"Cloning {full_name} @ {commit_sha}", secrets)

    # Prefer authenticated HTTPS via extraHeader so the remote URL never embeds the token.
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    clone_url = f"https://github.com/{full_name}.git"
    cmd = ["git", "clone", "--no-checkout", clone_url, str(repo_dir)]
    if github_token:
        basic = base64.b64encode(f"x-access-token:{github_token}".encode()).decode()
        cmd = [
            "git",
            "-c",
            f"http.extraHeader=AUTHORIZATION: basic {basic}",
            "clone",
            "--no-checkout",
            clone_url,
            str(repo_dir),
        ]

    result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    append_log(workspace, result.stdout or "", secrets)
    append_log(workspace, result.stderr or "", secrets)
    if result.returncode != 0:
        raise WorkspaceError("git clone failed")

    # Ensure remote has no credential-bearing URL
    subprocess.run(
        ["git", "remote", "set-url", "origin", clone_url],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
        prefixed = list(args)
        if github_token:
            basic = base64.b64encode(f"x-access-token:{github_token}".encode()).decode()
            prefixed = ["git", "-c", f"http.extraHeader=AUTHORIZATION: basic {basic}", *args[1:]]
        return subprocess.run(prefixed, cwd=repo_dir, capture_output=True, text=True, env=env, check=False)

    fetch = _git(["git", "fetch", "--depth", "1", "origin", commit_sha])
    append_log(workspace, fetch.stdout or "", secrets)
    append_log(workspace, fetch.stderr or "", secrets)
    # Deepen if shallow fetch by SHA fails: fetch more history
    if fetch.returncode != 0:
        append_log(workspace, "Shallow fetch by SHA failed; fetching full history", secrets)
        fetch2 = _git(["git", "fetch", "--unshallow"])
        append_log(workspace, fetch2.stdout or "", secrets)
        append_log(workspace, fetch2.stderr or "", secrets)
        fetch3 = _git(["git", "fetch", "origin"])
        append_log(workspace, fetch3.stdout or "", secrets)
        append_log(workspace, fetch3.stderr or "", secrets)

    checkout = subprocess.run(
        ["git", "checkout", "--force", commit_sha],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    append_log(workspace, checkout.stdout or "", secrets)
    append_log(workspace, checkout.stderr or "", secrets)
    if checkout.returncode != 0:
        raise WorkspaceError("git checkout of exact SHA failed")

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    head_sha = (head.stdout or "").strip()
    append_log(workspace, f"HEAD={head_sha}", secrets)
    if head_sha != commit_sha:
        raise WorkspaceError(f"SHA mismatch: expected {commit_sha}, got {head_sha}")

    # Final remote hygiene check
    remotes = subprocess.run(
        ["git", "remote", "-v"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    remote_text = remotes.stdout or ""
    append_log(workspace, remote_text, secrets)
    if github_token and github_token in remote_text:
        raise WorkspaceError("Git remote still contains credentials after clone")

    return head_sha


def remove_workspace(path: str | Path | None) -> None:
    if not path:
        return
    p = Path(path)
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
