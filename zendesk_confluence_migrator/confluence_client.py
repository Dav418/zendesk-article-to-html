from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
from urllib3.util.retry import Retry

from zendesk_confluence_migrator.config import ConfluenceSettings, ExportSettings


class ConfluenceClient:
    def __init__(self, settings: ConfluenceSettings, export_settings: ExportSettings) -> None:
        self._settings = settings
        self._export_settings = export_settings
        self._wiki_base = f"{settings.base_url}/wiki"
        self._session = requests.Session()
        retry = Retry(
            total=5,
            connect=5,
            read=5,
            status=5,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "zendesk-confluence-migrator/2.0",
            }
        )
        if settings.oauth_token:
            self._session.headers["Authorization"] = f"Bearer {settings.oauth_token}"
        else:
            assert settings.email is not None
            assert settings.api_token is not None
            self._session.auth = HTTPBasicAuth(settings.email, settings.api_token)

    def get_space(self, space_key: str) -> dict[str, Any]:
        return self._request_json("GET", f"/rest/api/space/{space_key}")

    def get_page(self, page_id: str) -> dict[str, Any]:
        return self._request_json(
            "GET",
            f"/rest/api/content/{page_id}",
            params={"expand": "space,version,ancestors"},
        )

    def list_pages_in_space(self, space_key: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        start = 0
        limit = 100
        while True:
            payload = self._request_json(
                "GET",
                "/rest/api/content",
                params={
                    "spaceKey": space_key,
                    "type": "page",
                    "status": "current",
                    "limit": limit,
                    "start": start,
                },
            )
            page_results = payload.get("results", [])
            if not isinstance(page_results, list):
                raise RuntimeError("Confluence content response did not contain a results list")
            results.extend(item for item in page_results if isinstance(item, dict))
            size = int(payload.get("size", len(page_results)) or 0)
            links = payload.get("_links")
            if size == 0 or not (isinstance(links, dict) and links.get("next")):
                break
            start += size
        return results

    def create_page(
        self,
        *,
        space_key: str,
        title: str,
        parent_page_id: str,
        body_storage: str,
    ) -> dict[str, Any]:
        payload = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "ancestors": [{"id": str(parent_page_id)}],
            "body": {
                "storage": {
                    "value": body_storage,
                    "representation": "storage",
                }
            },
        }
        return self._request_json("POST", "/rest/api/content", json=payload)

    def update_page(
        self,
        *,
        page_id: str,
        space_key: str,
        title: str,
        body_storage: str,
    ) -> dict[str, Any]:
        current = self.get_page(page_id)
        version = current.get("version")
        if not isinstance(version, dict) or version.get("number") is None:
            raise RuntimeError(f"Confluence page {page_id} did not return a version number")
        payload = {
            "id": str(page_id),
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {
                "storage": {
                    "value": body_storage,
                    "representation": "storage",
                }
            },
            "version": {
                "number": int(version["number"]) + 1,
                "minorEdit": True,
                "message": "Zendesk Help Center migration",
            },
        }
        return self._request_json("PUT", f"/rest/api/content/{page_id}", json=payload)

    def upload_attachment(self, *, page_id: str, file_path: Path) -> dict[str, Any]:
        url = f"{self._wiki_base}/rest/api/content/{page_id}/child/attachment"
        headers = {"X-Atlassian-Token": "nocheck"}
        with file_path.open("rb") as handle:
            response = self._session.put(
                url,
                headers=headers,
                files={"file": (file_path.name, handle)},
                data={
                    "minorEdit": "true",
                    "comment": "Migrated from Zendesk Help Center",
                },
                timeout=self._export_settings.request_timeout_seconds,
            )
        self._raise_for_response(response, operation=f"upload attachment {file_path.name}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Confluence returned a non-object attachment response")
        return payload

    def page_url(self, page: dict[str, Any], *, space_key: str | None = None) -> str:
        links = page.get("_links")
        if isinstance(links, dict) and links.get("webui"):
            webui = str(links["webui"])
            if webui.startswith("/wiki/"):
                return f"{self._settings.base_url}{webui}"
            return urljoin(f"{self._wiki_base}/", webui.lstrip("/"))
        page_id = str(page.get("id") or "")
        key = space_key or self._settings.space_key
        return f"{self._wiki_base}/spaces/{key}/pages/{page_id}"

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._session.request(
            method,
            f"{self._wiki_base}{path}",
            params=params,
            json=json,
            headers={"Content-Type": "application/json"} if json is not None else None,
            timeout=self._export_settings.request_timeout_seconds,
        )
        self._raise_for_response(response, operation=f"{method} {path}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Confluence returned a non-object JSON response")
        return payload

    def _raise_for_response(self, response: requests.Response, *, operation: str) -> None:
        if response.status_code == 401:
            raise RuntimeError(
                "Confluence returned 401 Unauthorized. Check the Confluence API/OAuth token "
                "and email."
            )
        if response.status_code == 403:
            raise RuntimeError(
                "Confluence returned 403 Forbidden. The authenticated user may not have the "
                "required permission in the target space/page."
            )
        if response.status_code == 404:
            raise RuntimeError(
                f"Confluence returned 404 for {operation}. Check the site URL, space key, "
                "and parent/page IDs."
            )
        if response.status_code >= 400:
            body = response.text[:2000]
            raise RuntimeError(
                f"Confluence {operation} failed with HTTP {response.status_code}: {body}"
            )
