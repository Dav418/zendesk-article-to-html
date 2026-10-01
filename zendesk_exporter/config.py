from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


_CATEGORY_PATH_RE = re.compile(
    r"^/hc/(?P<locale>[^/]+)/categories/(?P<category_id>\d+)(?:-[^/?#]*)?/?$",
    re.IGNORECASE,
)


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


@dataclass(frozen=True)
class ExportConfig:
    category_url: str
    zendesk_origin: str
    zendesk_host: str
    locale: str
    category_id: int
    zendesk_email: str | None
    zendesk_api_token: str | None
    zendesk_oauth_token: str | None
    confluence_space_name: str | None
    include_drafts: bool
    allow_partial_export: bool
    fail_on_unresolved_zendesk_links: bool
    request_timeout_seconds: int
    output_dir: Path

    @classmethod
    def from_environment(cls) -> "ExportConfig":
        load_dotenv()

        category_url = os.getenv("ZENDESK_CATEGORY_URL", "").strip()
        if not category_url:
            raise ValueError("ZENDESK_CATEGORY_URL is required in .env")

        parsed = urlparse(category_url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("ZENDESK_CATEGORY_URL must be a full http(s) URL")

        match = _CATEGORY_PATH_RE.match(parsed.path)
        if not match:
            raise ValueError(
                "ZENDESK_CATEGORY_URL must look like "
                "https://company.zendesk.com/hc/en-gb/categories/123456-category-name"
            )

        oauth_token = os.getenv("ZENDESK_OAUTH_TOKEN", "").strip() or None
        email = os.getenv("ZENDESK_EMAIL", "").strip() or None
        api_token = os.getenv("ZENDESK_API_TOKEN", "").strip() or None

        if not oauth_token and not (email and api_token):
            raise ValueError(
                "Configure either ZENDESK_OAUTH_TOKEN, or both ZENDESK_EMAIL "
                "and ZENDESK_API_TOKEN in .env"
            )

        timeout_raw = os.getenv("REQUEST_TIMEOUT_SECONDS", "30").strip()
        try:
            timeout = int(timeout_raw)
        except ValueError as exc:
            raise ValueError("REQUEST_TIMEOUT_SECONDS must be an integer") from exc
        if timeout <= 0:
            raise ValueError("REQUEST_TIMEOUT_SECONDS must be greater than zero")

        output_dir = Path(os.getenv("OUTPUT_DIR", "output").strip() or "output")

        origin = f"{parsed.scheme}://{parsed.netloc}"
        return cls(
            category_url=category_url,
            zendesk_origin=origin,
            zendesk_host=parsed.hostname.lower(),
            locale=match.group("locale"),
            category_id=int(match.group("category_id")),
            zendesk_email=email,
            zendesk_api_token=api_token,
            zendesk_oauth_token=oauth_token,
            confluence_space_name=(
                os.getenv("CONFLUENCE_SPACE_NAME", "").strip() or None
            ),
            include_drafts=_parse_bool(os.getenv("INCLUDE_DRAFTS"), True),
            allow_partial_export=_parse_bool(
                os.getenv("ALLOW_PARTIAL_EXPORT"), False
            ),
            fail_on_unresolved_zendesk_links=_parse_bool(
                os.getenv("FAIL_ON_UNRESOLVED_ZENDESK_LINKS"), False
            ),
            request_timeout_seconds=timeout,
            output_dir=output_dir,
        )
