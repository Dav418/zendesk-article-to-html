from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse, urlunparse


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_ARTICLE_ID_RE = re.compile(r"/(?:hc/[^/]+/)?articles/(\d+)(?:[-/?#]|$)", re.IGNORECASE)
_API_ARTICLE_ID_RE = re.compile(
    r"/api/v2/help_center/(?:[^/]+/)?articles/(\d+)(?:\.json)?(?:[/?#]|$)",
    re.IGNORECASE,
)
_SECTION_ID_RE = re.compile(r"/(?:hc/[^/]+/)?sections/(\d+)(?:[-/?#]|$)", re.IGNORECASE)
_CATEGORY_ID_RE = re.compile(r"/(?:hc/[^/]+/)?categories/(\d+)(?:[-/?#]|$)", re.IGNORECASE)
_ATTACHMENT_ID_RE = re.compile(r"/hc/article_attachments/(\d+)(?:/|$)", re.IGNORECASE)


def safe_filename(value: str, *, fallback: str, max_length: int = 140) -> str:
    cleaned = html.unescape(value).strip()
    cleaned = _INVALID_FILENAME_CHARS.sub("-", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned)
    cleaned = cleaned.strip(" .-")
    if not cleaned:
        cleaned = fallback
    if cleaned.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"{cleaned}-page"
    if len(cleaned) > max_length:
        digest = short_hash(cleaned)
        cleaned = f"{cleaned[: max_length - len(digest) - 3].rstrip()} - {digest}"
    return cleaned


def short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]


def filename_from_url(url: str) -> str:
    return Path(unquote(urlparse(url).path)).name


def normalize_url(origin: str, url: str) -> str:
    parsed = urlparse(urljoin(origin, url))
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            "",
            parsed.query,
            "",
        )
    )


def extract_article_id(url: str) -> int | None:
    path = urlparse(url).path
    match = _ARTICLE_ID_RE.search(path) or _API_ARTICLE_ID_RE.search(path)
    return int(match.group(1)) if match else None


def extract_section_id(url: str) -> int | None:
    match = _SECTION_ID_RE.search(urlparse(url).path)
    return int(match.group(1)) if match else None


def extract_category_id(url: str) -> int | None:
    match = _CATEGORY_ID_RE.search(urlparse(url).path)
    return int(match.group(1)) if match else None


def extract_attachment_id(url: str) -> int | None:
    match = _ATTACHMENT_ID_RE.search(urlparse(url).path)
    return int(match.group(1)) if match else None


def bounded_title(value: str, *, fallback: str, max_length: int = 250) -> str:
    cleaned = re.sub(r"\s+", " ", html.unescape(value)).strip() or fallback
    if len(cleaned) <= max_length:
        return cleaned
    digest = short_hash(cleaned)
    return f"{cleaned[: max_length - len(digest) - 3].rstrip()} - {digest}"


def unique_title(
    base: str,
    *,
    suffix_hint: str,
    used_casefolded: set[str],
) -> str:
    base = bounded_title(base, fallback=f"Zendesk {suffix_hint}")
    candidate = base
    if candidate.casefold() not in used_casefolded:
        used_casefolded.add(candidate.casefold())
        return candidate

    suffix = f" (Zendesk {suffix_hint})"
    trimmed = base[: max(1, 250 - len(suffix))].rstrip()
    candidate = f"{trimmed}{suffix}"
    serial = 2
    while candidate.casefold() in used_casefolded:
        suffix = f" (Zendesk {suffix_hint}-{serial})"
        trimmed = base[: max(1, 250 - len(suffix))].rstrip()
        candidate = f"{trimmed}{suffix}"
        serial += 1
    used_casefolded.add(candidate.casefold())
    return candidate
