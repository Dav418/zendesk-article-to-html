from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
from urllib3.util.retry import Retry

from zendesk_confluence_migrator.config import ExportSettings, ZendeskSettings


class ZendeskClient:
    def __init__(self, settings: ZendeskSettings, export_settings: ExportSettings) -> None:
        self._settings = settings
        self._export_settings = export_settings
        self._authenticated_session = self._build_session(authenticated=True)
        self._public_session = self._build_session(authenticated=False)

    def _build_session(self, *, authenticated: bool) -> requests.Session:
        session = requests.Session()
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
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "zendesk-confluence-migrator/2.0",
            }
        )

        if authenticated:
            if self._settings.oauth_token:
                session.headers["Authorization"] = f"Bearer {self._settings.oauth_token}"
            else:
                assert self._settings.email is not None
                assert self._settings.api_token is not None
                session.auth = HTTPBasicAuth(
                    f"{self._settings.email}/token",
                    self._settings.api_token,
                )
        return session

    def get_category(self) -> dict[str, Any]:
        url = (
            f"{self._settings.origin}/api/v2/help_center/{self._settings.locale}/"
            f"categories/{self._settings.category_id}.json"
        )
        payload = self._get_json(url)
        category = payload.get("category")
        if not isinstance(category, dict):
            raise RuntimeError("Zendesk category response did not contain a category")
        return category

    def list_sections(self) -> list[dict[str, Any]]:
        url = (
            f"{self._settings.origin}/api/v2/help_center/{self._settings.locale}/"
            f"categories/{self._settings.category_id}/sections.json"
        )
        return list(self._paginate(url, "sections"))

    def list_articles(self) -> list[dict[str, Any]]:
        url = (
            f"{self._settings.origin}/api/v2/help_center/{self._settings.locale}/"
            f"categories/{self._settings.category_id}/articles.json"
        )
        return list(self._paginate(url, "articles"))

    def list_article_attachments(self, article_id: int) -> list[dict[str, Any]]:
        url = (
            f"{self._settings.origin}/api/v2/help_center/{self._settings.locale}/"
            f"articles/{article_id}/attachments.json"
        )
        return list(self._paginate(url, "article_attachments"))

    def download(self, url: str) -> tuple[bytes, str | None, str]:
        absolute_url = urljoin(self._settings.origin, url)
        parsed = urlparse(absolute_url)
        session = (
            self._authenticated_session
            if parsed.hostname and parsed.hostname.lower() == self._settings.host
            else self._public_session
        )
        response = session.get(
            absolute_url,
            timeout=self._export_settings.request_timeout_seconds,
            stream=True,
        )
        response.raise_for_status()
        return response.content, response.headers.get("Content-Type"), response.url

    def _paginate(self, url: str, collection_key: str) -> Iterator[dict[str, Any]]:
        next_url: str | None = url
        params: dict[str, Any] | None = {"page[size]": 100}

        while next_url:
            payload = self._get_json(next_url, params=params)
            params = None
            items = payload.get(collection_key, [])
            if not isinstance(items, list):
                raise RuntimeError(f"Zendesk response field {collection_key!r} was not a list")
            for item in items:
                if isinstance(item, dict):
                    yield item

            meta = payload.get("meta")
            links = payload.get("links")
            if isinstance(meta, dict) and "has_more" in meta:
                if not meta.get("has_more"):
                    next_url = None
                elif isinstance(links, dict) and links.get("next"):
                    next_url = str(links["next"])
                else:
                    raise RuntimeError(
                        "Zendesk cursor pagination reported more data but did not provide links.next"
                    )
                continue

            raw_next_page = payload.get("next_page")
            next_url = str(raw_next_page) if raw_next_page else None

    def _get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._assert_zendesk_url(url)
        response = self._authenticated_session.get(
            url,
            params=params,
            timeout=self._export_settings.request_timeout_seconds,
        )
        if response.status_code == 401:
            raise RuntimeError(
                "Zendesk returned 401 Unauthorized. Check the Zendesk OAuth/API token and email."
            )
        if response.status_code == 403:
            raise RuntimeError(
                "Zendesk returned 403 Forbidden. The authenticated user may not be allowed "
                "to read this Help Center content."
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Zendesk returned a non-object JSON response")
        return payload

    def _assert_zendesk_url(self, url: str) -> None:
        parsed = urlparse(url)
        if not parsed.hostname or parsed.hostname.lower() != self._settings.host:
            logging.error("Refusing authenticated request to unexpected host: %s", url)
            raise RuntimeError("Zendesk pagination/API URL changed to an unexpected host")
