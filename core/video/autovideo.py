"""HTTP client for AutoVideo, the local text-to-video service.

Follows the contract AutoVideo publishes for upstream callers
(``autovideo/UPSTREAM_INTEGRATION.md``): create a job, keep the returned job
id, poll until a terminal status, download the listed artifacts and check
each against the size and SHA-256 AutoVideo reports. Ported from
video-opportunity-desk, where it rendered real videos end to end.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Optional

import httpx

TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
# Same shape AutoVideo enforces; checked again because it becomes a file name.
ARTIFACT_NAME = re.compile(r"^[a-z][a-z0-9-]{0,40}\.(?:mp4|srt|json|txt)$")


class AutoVideoError(Exception):
    """AutoVideo answered, and the answer was an error."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message


class AutoVideoUnreachable(Exception):
    """No usable answer. For a create request the outcome is unknown."""


class AutoVideoClient:
    def __init__(self, base_url: str, *, timeout: float = 30.0,
                 transport: Optional[httpx.BaseTransport] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=self._timeout, transport=self._transport)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            with self._client() as client:
                response = client.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise AutoVideoUnreachable(f"AutoVideo 无响应（{self.base_url}）：{type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise _error_from(response)
        try:
            return response.json()
        except ValueError as exc:
            raise AutoVideoError(response.status_code, "BAD_RESPONSE", "AutoVideo 返回了无法解析的内容") from exc

    def health(self) -> dict:
        return self._request("GET", "/api/health")

    def config_status(self) -> dict:
        return self._request("GET", "/api/config/status")

    def voices(self) -> list[dict]:
        return self._request("GET", "/api/voices").get("items", [])

    def create_job(self, payload: dict) -> dict:
        return self._request("POST", "/api/jobs", json=payload)

    def get_job(self, job_id: str) -> dict:
        return self._request("GET", f"/api/jobs/{job_id}")

    def find_jobs(self, query: str) -> list[dict]:
        data = self._request("GET", "/api/jobs", params={"q": query[:200], "limit": 50})
        return list(data.get("items", []))

    def list_artifacts(self, job_id: str) -> list[dict]:
        return list(self._request("GET", f"/api/jobs/{job_id}/artifacts").get("items", []))

    def job_page_url(self, job_id: str) -> str:
        return f"{self.base_url}/#job/{job_id}"

    def download(self, artifact: dict, dest: Path) -> None:
        """Download one artifact to ``dest``, verifying size and SHA-256.

        Writes a ``.part`` file first, so a failed or mismatched download never
        leaves a file that looks complete.
        """
        url = str(artifact.get("download_url", ""))
        # Relative to the AutoVideo base. An absolute URL would make httpx
        # ignore the base and fetch from wherever it points.
        if not url.startswith("/api/"):
            raise AutoVideoError(502, "BAD_ARTIFACT_URL", f"不接受的下载地址：{url!r}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        digest = hashlib.sha256()
        size = 0
        try:
            with self._client() as client, client.stream("GET", url) as response:
                if response.status_code >= 400:
                    response.read()
                    raise _error_from(response)
                with tmp.open("wb") as fh:
                    for chunk in response.iter_bytes():
                        fh.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
        except httpx.TransportError as exc:
            tmp.unlink(missing_ok=True)
            raise AutoVideoUnreachable(f"下载 {dest.name} 时连接中断") from exc
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        expected_sha, expected_bytes = artifact.get("sha256"), artifact.get("bytes")
        if (expected_sha and digest.hexdigest() != expected_sha) or (
            expected_bytes is not None and size != expected_bytes
        ):
            tmp.unlink(missing_ok=True)
            raise AutoVideoError(502, "ARTIFACT_MISMATCH", f"{dest.name} 的大小或哈希与 AutoVideo 记录不一致")
        os.replace(tmp, dest)


def _error_from(response: httpx.Response) -> AutoVideoError:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, dict):
        return AutoVideoError(response.status_code, str(detail.get("code") or f"HTTP_{response.status_code}"),
                              str(detail.get("message") or ""))
    if isinstance(detail, list):
        msgs = "; ".join(str(i.get("msg", i)) for i in detail if isinstance(i, dict))
        return AutoVideoError(response.status_code, "VALIDATION_ERROR", msgs or str(detail))
    return AutoVideoError(response.status_code, f"HTTP_{response.status_code}", response.text.strip()[:300])
