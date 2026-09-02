from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings


class GitHubAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class GitHubService:
    API = "https://api.github.com"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    DEFAULT_TIMEOUT = 60.0

    def __init__(self, access_token: str | None = None):
        self.access_token = access_token
        self.settings = get_settings()

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.DEFAULT_TIMEOUT)

    def _request_with_retry(self, method: str, url: str, **kwargs):
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with self._client() as client:
                    return client.request(method, url, **kwargs)
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
                last_exc = exc
                if attempt == 2:
                    raise
        raise last_exc  # pragma: no cover

    def _headers(self, token: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Mock-Odoo-sh-Local",
        }
        tok = token or self.access_token
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        return headers

    def exchange_code(self, code: str, *, redirect_uri: str | None = None) -> str:
        uri = redirect_uri or self.settings.github_callback_url
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                self.TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.settings.github_client_id,
                    "client_secret": self.settings.github_client_secret,
                    "code": code,
                    "redirect_uri": uri,
                },
            )
        data = resp.json()
        if resp.status_code >= 400 or "error" in data:
            raise GitHubAPIError(data.get("error_description") or data.get("error") or "OAuth exchange failed")
        token = data.get("access_token")
        if not token:
            raise GitHubAPIError("GitHub did not return an access token")
        return token

    def get_current_user(self, token: str | None = None) -> dict[str, Any]:
        with httpx.Client(timeout=60.0) as client:
            resp = client.get(f"{self.API}/user", headers=self._headers(token))
            if resp.status_code == 401:
                raise GitHubAPIError("Invalid or expired GitHub token", 401)
            if resp.status_code == 403:
                raise GitHubAPIError("GitHub rate limit or access denied", 403)
            if resp.status_code >= 400:
                raise GitHubAPIError(f"GitHub user fetch failed ({resp.status_code})", resp.status_code)
            user = resp.json()
            email = user.get("email")
            if not email:
                emails_resp = client.get(f"{self.API}/user/emails", headers=self._headers(token))
                if emails_resp.status_code == 200:
                    emails = emails_resp.json()
                    primary = next((e for e in emails if e.get("primary") and e.get("verified")), None)
                    email = (primary or (emails[0] if emails else {})).get("email")
            user["email"] = email
            return user

    def list_repositories(self, token: str | None = None) -> list[dict[str, Any]]:
        repos: list[dict[str, Any]] = []
        page = 1
        with httpx.Client(timeout=60.0) as client:
            while page <= 5:
                resp = client.get(
                    f"{self.API}/user/repos",
                    headers=self._headers(token),
                    params={
                        "per_page": 100,
                        "page": page,
                        "sort": "updated",
                        "direction": "desc",
                        "affiliation": "owner,collaborator,organization_member",
                    },
                )
                if resp.status_code == 401:
                    raise GitHubAPIError("Invalid or expired GitHub token", 401)
                if resp.status_code == 403:
                    raise GitHubAPIError("GitHub rate limit or access denied", 403)
                if resp.status_code >= 400:
                    raise GitHubAPIError(f"Failed to list repositories ({resp.status_code})", resp.status_code)
                batch = resp.json()
                if not batch:
                    break
                for r in batch:
                    repos.append(
                        {
                            "id": str(r["id"]),
                            "name": r["name"],
                            "full_name": r["full_name"],
                            "private": bool(r.get("private")),
                            "html_url": r["html_url"],
                            "default_branch": r.get("default_branch") or "main",
                            "owner": (r.get("owner") or {}).get("login"),
                            "updated_at": r.get("updated_at"),
                        }
                    )
                if len(batch) < 100:
                    break
                page += 1
        return repos

    def get_repository(self, full_name: str, token: str | None = None) -> dict[str, Any]:
        with httpx.Client(timeout=60.0) as client:
            resp = client.get(f"{self.API}/repos/{full_name}", headers=self._headers(token))
        if resp.status_code == 404:
            raise GitHubAPIError("Repository not found or access denied", 404)
        if resp.status_code == 401:
            raise GitHubAPIError("Invalid or expired GitHub token", 401)
        if resp.status_code >= 400:
            raise GitHubAPIError(f"Failed to load repository ({resp.status_code})", resp.status_code)
        r = resp.json()
        return {
            "id": str(r["id"]),
            "name": r["name"],
            "full_name": r["full_name"],
            "private": bool(r.get("private")),
            "html_url": r["html_url"],
            "default_branch": r.get("default_branch") or "main",
            "owner": (r.get("owner") or {}).get("login"),
        }

    def list_branches(self, full_name: str, token: str | None = None) -> list[dict[str, Any]]:
        branches: list[dict[str, Any]] = []
        page = 1
        with httpx.Client(timeout=60.0) as client:
            while page <= 10:
                resp = client.get(
                    f"{self.API}/repos/{full_name}/branches",
                    headers=self._headers(token),
                    params={"per_page": 100, "page": page},
                )
                if resp.status_code == 404:
                    raise GitHubAPIError("Repository not found or access denied", 404)
                if resp.status_code == 401:
                    raise GitHubAPIError("Invalid or expired GitHub token", 401)
                if resp.status_code == 403:
                    raise GitHubAPIError("GitHub rate limit or access denied", 403)
                if resp.status_code >= 400:
                    raise GitHubAPIError(f"Failed to list branches ({resp.status_code})", resp.status_code)
                batch = resp.json()
                if not batch:
                    break
                for b in batch:
                    commit = b.get("commit") or {}
                    branches.append(
                        {
                            "name": b["name"],
                            "sha": commit.get("sha") or "",
                            "protected": bool(b.get("protected")),
                        }
                    )
                if len(batch) < 100:
                    break
                page += 1
        return branches

    def list_webhooks(self, full_name: str, token: str | None = None) -> list[dict[str, Any]]:
        resp = self._request_with_retry(
            "GET",
            f"{self.API}/repos/{full_name}/hooks",
            headers=self._headers(token),
            params={"per_page": 100},
        )
        if resp.status_code in (403, 404):
            raise GitHubAPIError(
                "Automatic builds could not be enabled. Manual builds remain available.",
                resp.status_code,
            )
        if resp.status_code >= 400:
            raise GitHubAPIError(f"Failed to list webhooks ({resp.status_code})", resp.status_code)
        return list(resp.json() or [])

    def create_webhook(
        self,
        full_name: str,
        *,
        callback_url: str,
        secret: str,
        token: str | None = None,
        events: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "name": "web",
            "active": True,
            "events": events or ["push"],
            "config": {
                "url": callback_url,
                "content_type": "json",
                "secret": secret,
                "insecure_ssl": "0",
            },
        }
        resp = self._request_with_retry(
            "POST",
            f"{self.API}/repos/{full_name}/hooks",
            headers=self._headers(token),
            json=payload,
        )
        if resp.status_code in (403, 404):
            raise GitHubAPIError(
                "Automatic builds could not be enabled. Manual builds remain available.",
                resp.status_code,
            )
        if resp.status_code >= 400:
            raise GitHubAPIError(
                f"Failed to create webhook ({resp.status_code}): {resp.text[:200]}",
                resp.status_code,
            )
        return resp.json()

    def delete_webhook(self, full_name: str, hook_id: str | int, token: str | None = None) -> None:
        resp = self._request_with_retry(
            "DELETE",
            f"{self.API}/repos/{full_name}/hooks/{hook_id}",
            headers=self._headers(token),
        )
        if resp.status_code in (204, 404):
            return
        if resp.status_code in (403,):
            raise GitHubAPIError(
                "Could not remove GitHub webhook (permission denied).",
                resp.status_code,
            )
        if resp.status_code >= 400:
            raise GitHubAPIError(f"Failed to delete webhook ({resp.status_code})", resp.status_code)

    def find_hook_by_url(
        self, full_name: str, callback_url: str, token: str | None = None
    ) -> dict[str, Any] | None:
        for hook in self.list_webhooks(full_name, token=token):
            cfg = hook.get("config") or {}
            if cfg.get("url") == callback_url:
                return hook
        return None
