from __future__ import annotations

import csv
import hashlib
import html
import json
import logging
import mimetypes
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from zendesk_exporter.config import ExportConfig
from zendesk_exporter.zendesk_client import ZendeskClient


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_ARTICLE_ID_RE = re.compile(r"/(?:hc/[^/]+/)?articles/(\d+)(?:[-/?#]|$)", re.IGNORECASE)
_API_ARTICLE_ID_RE = re.compile(
    r"/api/v2/help_center/(?:[^/]+/)?articles/(\d+)(?:\.json)?(?:[/?#]|$)",
    re.IGNORECASE,
)
_ATTACHMENT_ID_RE = re.compile(r"/hc/article_attachments/(\d+)(?:/|$)", re.IGNORECASE)
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class KnowledgeBaseExporter:
    def __init__(self, *, config: ExportConfig, client: ZendeskClient) -> None:
        self._config = config
        self._client = client
        self._issues: list[dict[str, Any]] = []
        self._article_rows: list[dict[str, Any]] = []

    def run(self) -> int:
        logging.info("Reading Zendesk category, sections, and articles...")
        category = self._client.get_category()
        sections = self._client.list_sections()
        articles = self._client.list_articles()

        if not self._config.include_drafts:
            articles = [article for article in articles if not bool(article.get("draft"))]

        if not articles:
            raise RuntimeError(
                "Zendesk returned zero articles for this category and locale. "
                "Check the category URL and make sure the authenticated account can "
                "see the required articles before importing anything into Confluence."
            )

        category_name = str(category.get("name") or f"Category {self._config.category_id}")
        space_name = self._config.confluence_space_name or category_name
        safe_space_name = self._safe_filename(space_name, fallback="Zendesk Knowledge Base")

        output_root = self._config.output_dir.resolve()
        space_dir = output_root / safe_space_name
        zip_path = output_root / f"{safe_space_name}-confluence-import.zip"
        report_csv_path = output_root / "migration-report.csv"
        report_json_path = output_root / "migration-report.json"

        self._prepare_output(output_root, space_dir, zip_path, report_csv_path, report_json_path)

        section_by_id = {
            int(section["id"]): section
            for section in sections
            if section.get("id") is not None
        }

        logging.info(
            "Found %d article(s) in %d section(s) for locale %s.",
            len(articles),
            len(sections),
            self._config.locale,
        )

        article_stems = self._build_article_stems(articles)
        article_file_by_id = {
            int(article["id"]): f"{article_stems[int(article['id'])]}.html"
            for article in articles
            if article.get("id") is not None
        }
        used_page_stems = {stem.casefold() for stem in article_stems.values()}
        category_index_stem = self._unique_page_stem(
            f"{category_name} - Index",
            fallback="Knowledge Base - Index",
            suffix_hint=str(self._config.category_id),
            used_stems=used_page_stems,
        )
        section_index_file_by_id = self._build_section_index_files(
            sections,
            used_page_stems,
        )

        sorted_articles = sorted(
            articles,
            key=lambda article: self._article_sort_key(article, section_by_id),
        )

        articles_by_section: dict[int | None, list[dict[str, Any]]] = defaultdict(list)
        for article in sorted_articles:
            section_id = self._as_int(article.get("section_id"))
            articles_by_section[section_id].append(article)

        self._write_category_index(
            space_dir=space_dir,
            category_name=category_name,
            category_description=str(category.get("description") or ""),
            sections=sections,
            articles_by_section=articles_by_section,
            category_index_stem=category_index_stem,
            section_index_file_by_id=section_index_file_by_id,
        )
        self._write_section_indexes(
            space_dir=space_dir,
            sections=sections,
            articles_by_section=articles_by_section,
            article_file_by_id=article_file_by_id,
            section_index_file_by_id=section_index_file_by_id,
        )

        article_failures = 0
        for index, article in enumerate(sorted_articles, start=1):
            article_id = self._as_int(article.get("id"))
            title = str(article.get("title") or f"Article {article_id or index}")
            logging.info("[%d/%d] %s", index, len(sorted_articles), title)
            try:
                self._export_article(
                    article=article,
                    section=section_by_id.get(self._as_int(article.get("section_id"))),
                    page_stem=article_stems[article_id],
                    article_file_by_id=article_file_by_id,
                    space_dir=space_dir,
                )
            except Exception as exc:  # noqa: BLE001 - continue to report all failures
                article_failures += 1
                logging.exception("Failed to export article %s: %s", article_id, exc)
                self._issues.append(
                    {
                        "severity": "error",
                        "type": "article_export_failed",
                        "article_id": article_id,
                        "title": title,
                        "url": article.get("html_url"),
                        "detail": str(exc),
                    }
                )

        summary = self._build_summary(
            category=category,
            sections=sections,
            articles=articles,
            article_failures=article_failures,
            safe_space_name=safe_space_name,
            space_dir=space_dir,
            zip_path=zip_path,
        )
        self._write_reports(report_csv_path, report_json_path, summary)

        blocking_issues = article_failures > 0 or any(
            issue.get("severity") == "error" for issue in self._issues
        )
        if self._config.fail_on_unresolved_zendesk_links:
            blocking_issues = blocking_issues or any(
                issue.get("type") == "unresolved_zendesk_article_link"
                for issue in self._issues
            )

        if blocking_issues and not self._config.allow_partial_export:
            logging.error(
                "Export contains blocking errors. No Confluence ZIP was created. "
                "Review %s and %s.",
                report_csv_path,
                report_json_path,
            )
            self._print_summary(summary, zip_created=False)
            return 2

        self._create_zip(space_dir=space_dir, zip_path=zip_path)
        summary["zip_created"] = True
        summary["zip_size_bytes"] = zip_path.stat().st_size
        self._write_reports(report_csv_path, report_json_path, summary)
        self._print_summary(summary, zip_created=True)
        return 0

    def _export_article(
        self,
        *,
        article: dict[str, Any],
        section: dict[str, Any] | None,
        page_stem: str,
        article_file_by_id: dict[int, str],
        space_dir: Path,
    ) -> None:
        article_id = self._required_int(article, "id")
        title = str(article.get("title") or f"Article {article_id}")
        body = str(article.get("body") or "")
        media_dir = space_dir / page_stem
        page_path = space_dir / f"{page_stem}.html"

        attachments = self._client.list_article_attachments(article_id)
        downloaded_attachment_paths: dict[int, str] = {}
        attachment_url_to_path: dict[str, str] = {}
        used_media_names: set[str] = set()
        failed_assets = 0

        for attachment in attachments:
            attachment_id = self._as_int(attachment.get("id"))
            content_url = str(attachment.get("content_url") or "").strip()
            if attachment_id is None or not content_url:
                continue

            original_name = str(attachment.get("file_name") or f"attachment-{attachment_id}")
            media_name = self._unique_media_name(
                original_name,
                used_media_names,
                suffix_hint=str(attachment_id),
            )
            try:
                content, content_type, _ = self._download(content_url)
                if "." not in Path(media_name).name:
                    media_name = self._append_extension(media_name, content_type)
                    media_name = self._unique_media_name(
                        media_name,
                        used_media_names,
                        suffix_hint=str(attachment_id),
                    )
                media_dir.mkdir(parents=True, exist_ok=True)
                (media_dir / media_name).write_bytes(content)
                relative_path = self._quoted_relative_media_path(page_stem, media_name)
                downloaded_attachment_paths[attachment_id] = relative_path
                attachment_url_to_path[self._normalize_url(content_url)] = relative_path
            except Exception as exc:  # noqa: BLE001
                failed_assets += 1
                self._issues.append(
                    {
                        "severity": "error",
                        "type": "attachment_download_failed",
                        "article_id": article_id,
                        "title": title,
                        "url": content_url,
                        "detail": str(exc),
                    }
                )

        soup = BeautifulSoup(body, "html.parser")
        referenced_attachment_ids: set[int] = set()
        inline_images_downloaded = 0
        internal_links_rewritten = 0
        unresolved_internal_links = 0

        for image_index, image_tag in enumerate(soup.find_all("img"), start=1):
            source = (
                image_tag.get("src")
                or image_tag.get("data-original")
                or image_tag.get("data-src")
            )
            if not source or str(source).startswith("data:"):
                continue

            absolute_source = urljoin(
                str(article.get("html_url") or self._config.zendesk_origin),
                str(source),
            )
            attachment_id = self._extract_attachment_id(absolute_source)
            local_path = None

            if attachment_id is not None:
                local_path = downloaded_attachment_paths.get(attachment_id)
                if local_path:
                    referenced_attachment_ids.add(attachment_id)

            if local_path is None:
                local_path = attachment_url_to_path.get(
                    self._normalize_url(absolute_source)
                )

            if local_path is None:
                try:
                    content, content_type, final_url = self._download(absolute_source)
                    file_name = self._filename_from_url(final_url)
                    if not file_name:
                        file_name = f"image-{image_index}"
                    if "." not in Path(file_name).name:
                        file_name = self._append_extension(file_name, content_type)
                    file_name = self._unique_media_name(
                        file_name,
                        used_media_names,
                        suffix_hint=self._short_hash(absolute_source),
                    )
                    media_dir.mkdir(parents=True, exist_ok=True)
                    (media_dir / file_name).write_bytes(content)
                    local_path = self._quoted_relative_media_path(page_stem, file_name)
                    inline_images_downloaded += 1
                except Exception as exc:  # noqa: BLE001
                    failed_assets += 1
                    self._issues.append(
                        {
                            "severity": "error",
                            "type": "inline_image_download_failed",
                            "article_id": article_id,
                            "title": title,
                            "url": absolute_source,
                            "detail": str(exc),
                        }
                    )
                    continue

            image_tag["src"] = local_path
            image_tag.attrs.pop("srcset", None)
            image_tag.attrs.pop("data-original", None)
            image_tag.attrs.pop("data-src", None)

        for anchor in soup.find_all("a"):
            href = anchor.get("href")
            if not href:
                continue
            href_text = str(href).strip()
            if not href_text or href_text.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue

            absolute_href = urljoin(
                str(article.get("html_url") or self._config.zendesk_origin),
                href_text,
            )

            attachment_id = self._extract_attachment_id(absolute_href)
            if attachment_id is not None and attachment_id in downloaded_attachment_paths:
                anchor["href"] = downloaded_attachment_paths[attachment_id]
                referenced_attachment_ids.add(attachment_id)
                continue

            normalized_href = self._normalize_url(absolute_href)
            if normalized_href in attachment_url_to_path:
                anchor["href"] = attachment_url_to_path[normalized_href]
                continue

            linked_article_id = self._extract_article_id(absolute_href)
            if linked_article_id is None:
                continue

            exported_file = article_file_by_id.get(linked_article_id)
            if exported_file:
                anchor["href"] = quote(exported_file)
                internal_links_rewritten += 1
            elif self._is_zendesk_host(absolute_href):
                unresolved_internal_links += 1
                self._issues.append(
                    {
                        "severity": "warning",
                        "type": "unresolved_zendesk_article_link",
                        "article_id": article_id,
                        "title": title,
                        "url": absolute_href,
                        "detail": (
                            "The linked Zendesk article is outside this export or was not "
                            "returned for the configured locale/permissions."
                        ),
                    }
                )

        non_inline_attachments = [
            attachment
            for attachment in attachments
            if not bool(attachment.get("inline"))
            and self._as_int(attachment.get("id")) in downloaded_attachment_paths
        ]
        if non_inline_attachments:
            heading = soup.new_tag("h2")
            heading.string = "Attachments"
            soup.append(heading)
            list_tag = soup.new_tag("ul")
            for attachment in non_inline_attachments:
                attachment_id = self._required_int(attachment, "id")
                item = soup.new_tag("li")
                link = soup.new_tag("a")
                link["href"] = downloaded_attachment_paths[attachment_id]
                link.string = str(
                    attachment.get("file_name") or f"Attachment {attachment_id}"
                )
                item.append(link)
                list_tag.append(item)
                referenced_attachment_ids.add(attachment_id)
            soup.append(list_tag)

        section_name = str(section.get("name")) if section else "Unknown section"
        wrapper = BeautifulSoup("<!doctype html><html><head></head><body></body></html>", "html.parser")
        meta = wrapper.new_tag("meta", charset="utf-8")
        wrapper.head.append(meta)

        section_marker = wrapper.new_tag("p")
        section_label = wrapper.new_tag("strong")
        section_label.string = "Zendesk section:"
        section_marker.append(section_label)
        section_marker.append(f" {section_name}")
        wrapper.body.append(section_marker)

        if bool(article.get("draft")):
            draft_marker = wrapper.new_tag("p")
            draft_label = wrapper.new_tag("strong")
            draft_label.string = "Zendesk status:"
            draft_marker.append(draft_label)
            draft_marker.append(" Draft")
            wrapper.body.append(draft_marker)

        wrapper.body.append(wrapper.new_tag("hr"))
        for node in list(soup.contents):
            wrapper.body.append(node)

        page_path.write_text(str(wrapper), encoding="utf-8")

        restricted = bool(article.get("user_segment_id")) or bool(
            article.get("user_segment_ids")
        )
        self._article_rows.append(
            {
                "article_id": article_id,
                "title": title,
                "section_id": self._as_int(article.get("section_id")),
                "section_name": section_name,
                "position": article.get("position"),
                "draft": bool(article.get("draft")),
                "restricted_in_zendesk": restricted,
                "source_url": article.get("html_url"),
                "export_file": page_path.name,
                "attachments_found": len(attachments),
                "attachments_downloaded": len(downloaded_attachment_paths),
                "inline_external_images_downloaded": inline_images_downloaded,
                "failed_assets": failed_assets,
                "internal_links_rewritten": internal_links_rewritten,
                "unresolved_internal_links": unresolved_internal_links,
            }
        )

    def _write_category_index(
        self,
        *,
        space_dir: Path,
        category_name: str,
        category_description: str,
        sections: list[dict[str, Any]],
        articles_by_section: dict[int | None, list[dict[str, Any]]],
        category_index_stem: str,
        section_index_file_by_id: dict[int, str],
    ) -> None:
        soup = BeautifulSoup("<!doctype html><html><head></head><body></body></html>", "html.parser")
        soup.head.append(soup.new_tag("meta", charset="utf-8"))
        heading = soup.new_tag("h1")
        heading.string = category_name
        soup.body.append(heading)

        if category_description.strip():
            description = BeautifulSoup(category_description, "html.parser")
            for node in list(description.contents):
                soup.body.append(node)

        intro = soup.new_tag("p")
        intro.string = "Sections exported from Zendesk:"
        soup.body.append(intro)
        listing = soup.new_tag("ul")
        for section in sorted(sections, key=self._section_sort_key):
            section_id = self._as_int(section.get("id"))
            item = soup.new_tag("li")
            section_page_name = section_index_file_by_id[section_id]
            link = soup.new_tag("a")
            link["href"] = quote(section_page_name)
            link.string = str(section.get("name") or f"Section {section_id}")
            item.append(link)
            item.append(f" ({len(articles_by_section.get(section_id, []))} articles)")
            listing.append(item)
        soup.body.append(listing)
        (space_dir / f"{category_index_stem}.html").write_text(str(soup), encoding="utf-8")

    def _write_section_indexes(
        self,
        *,
        space_dir: Path,
        sections: list[dict[str, Any]],
        articles_by_section: dict[int | None, list[dict[str, Any]]],
        article_file_by_id: dict[int, str],
        section_index_file_by_id: dict[int, str],
    ) -> None:
        for section in sorted(sections, key=self._section_sort_key):
            section_id = self._as_int(section.get("id"))
            section_name = str(section.get("name") or f"Section {section_id}")
            soup = BeautifulSoup("<!doctype html><html><head></head><body></body></html>", "html.parser")
            soup.head.append(soup.new_tag("meta", charset="utf-8"))
            heading = soup.new_tag("h1")
            heading.string = section_name
            soup.body.append(heading)

            description_text = str(section.get("description") or "")
            if description_text.strip():
                description = BeautifulSoup(description_text, "html.parser")
                for node in list(description.contents):
                    soup.body.append(node)

            listing = soup.new_tag("ol")
            for article in sorted(
                articles_by_section.get(section_id, []),
                key=lambda item: (
                    self._as_int(item.get("position")) or 0,
                    str(item.get("title") or "").casefold(),
                ),
            ):
                article_id = self._as_int(article.get("id"))
                if article_id is None:
                    continue
                item = soup.new_tag("li")
                link = soup.new_tag("a")
                link["href"] = quote(article_file_by_id[article_id])
                link.string = str(article.get("title") or f"Article {article_id}")
                item.append(link)
                if bool(article.get("draft")):
                    item.append(" [Draft]")
                listing.append(item)
            soup.body.append(listing)

            filename = section_index_file_by_id[section_id]
            (space_dir / filename).write_text(str(soup), encoding="utf-8")

    def _build_article_stems(self, articles: list[dict[str, Any]]) -> dict[int, str]:
        used: set[str] = set()
        result: dict[int, str] = {}
        for article in articles:
            article_id = self._required_int(article, "id")
            title = str(article.get("title") or f"Article {article_id}")
            base = self._safe_filename(title, fallback=f"Article {article_id}")
            candidate = base
            if candidate.casefold() in used:
                candidate = self._safe_filename(
                    f"{base} (Zendesk {article_id})",
                    fallback=f"Article {article_id}",
                )
            serial = 2
            while candidate.casefold() in used:
                candidate = self._safe_filename(
                    f"{base} (Zendesk {article_id}-{serial})",
                    fallback=f"Article {article_id}-{serial}",
                )
                serial += 1
            used.add(candidate.casefold())
            result[article_id] = candidate
        return result

    def _build_summary(
        self,
        *,
        category: dict[str, Any],
        sections: list[dict[str, Any]],
        articles: list[dict[str, Any]],
        article_failures: int,
        safe_space_name: str,
        space_dir: Path,
        zip_path: Path,
    ) -> dict[str, Any]:
        total_assets_failed = sum(int(row.get("failed_assets") or 0) for row in self._article_rows)
        unresolved_links = sum(
            int(row.get("unresolved_internal_links") or 0) for row in self._article_rows
        )
        restricted_articles = sum(
            1 for row in self._article_rows if row.get("restricted_in_zendesk")
        )
        drafts = sum(1 for row in self._article_rows if row.get("draft"))
        exported_files = list(space_dir.rglob("*")) if space_dir.exists() else []
        largest_file = max(
            (path for path in exported_files if path.is_file()),
            key=lambda path: path.stat().st_size,
            default=None,
        )

        return {
            "category": {
                "id": self._config.category_id,
                "name": category.get("name"),
                "source_url": self._config.category_url,
                "locale": self._config.locale,
            },
            "confluence_space_folder": safe_space_name,
            "articles_found": len(articles),
            "articles_exported": len(self._article_rows),
            "article_failures": article_failures,
            "sections_found": len(sections),
            "draft_articles_exported": drafts,
            "restricted_zendesk_articles": restricted_articles,
            "attachments_found": sum(
                int(row.get("attachments_found") or 0) for row in self._article_rows
            ),
            "attachments_downloaded": sum(
                int(row.get("attachments_downloaded") or 0) for row in self._article_rows
            ),
            "extra_inline_images_downloaded": sum(
                int(row.get("inline_external_images_downloaded") or 0)
                for row in self._article_rows
            ),
            "failed_assets": total_assets_failed,
            "internal_links_rewritten": sum(
                int(row.get("internal_links_rewritten") or 0)
                for row in self._article_rows
            ),
            "unresolved_zendesk_article_links": unresolved_links,
            "issues": self._issues,
            "output_directory": str(space_dir),
            "zip_path": str(zip_path),
            "zip_created": False,
            "largest_exported_file": (
                {
                    "path": str(largest_file.relative_to(space_dir)),
                    "size_bytes": largest_file.stat().st_size,
                }
                if largest_file
                else None
            ),
            "notes": [
                "The Confluence HTML ZIP importer creates pages from HTML files but "
                "does not document reconstruction of Zendesk section parent/child "
                "page hierarchy. Section index pages and per-article section labels are "
                "included so the section relationships are retained.",
                "Zendesk per-article visibility restrictions are reported but cannot be "
                "recreated by the HTML ZIP alone.",
                "Non-image attachments are included and linked from their article. "
                "Confluence may ignore unsupported supplemental media types.",
            ],
        }

    def _write_reports(
        self,
        csv_path: Path,
        json_path: Path,
        summary: dict[str, Any],
    ) -> None:
        csv_columns = [
            "article_id",
            "title",
            "section_id",
            "section_name",
            "position",
            "draft",
            "restricted_in_zendesk",
            "source_url",
            "export_file",
            "attachments_found",
            "attachments_downloaded",
            "inline_external_images_downloaded",
            "failed_assets",
            "internal_links_rewritten",
            "unresolved_internal_links",
        ]
        with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=csv_columns)
            writer.writeheader()
            writer.writerows(self._article_rows)

        json_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _create_zip(self, *, space_dir: Path, zip_path: Path) -> None:
        if zip_path.exists():
            zip_path.unlink()
        archive_base = str(zip_path.with_suffix(""))
        created = shutil.make_archive(
            archive_base,
            "zip",
            root_dir=space_dir.parent,
            base_dir=space_dir.name,
        )
        created_path = Path(created)
        if created_path != zip_path:
            if zip_path.exists():
                zip_path.unlink()
            created_path.replace(zip_path)

    def _prepare_output(
        self,
        output_root: Path,
        space_dir: Path,
        zip_path: Path,
        report_csv_path: Path,
        report_json_path: Path,
    ) -> None:
        output_root.mkdir(parents=True, exist_ok=True)
        if space_dir.exists():
            shutil.rmtree(space_dir)
        space_dir.mkdir(parents=True, exist_ok=True)
        for path in (zip_path, report_csv_path, report_json_path):
            if path.exists():
                path.unlink()

    def _print_summary(self, summary: dict[str, Any], *, zip_created: bool) -> None:
        print()
        print("=== Zendesk -> Confluence export summary ===")
        print(f"Articles found:              {summary['articles_found']}")
        print(f"Articles exported:           {summary['articles_exported']}")
        print(f"Article failures:            {summary['article_failures']}")
        print(f"Sections found:              {summary['sections_found']}")
        print(f"Attachments downloaded:      {summary['attachments_downloaded']}")
        print(f"Extra inline images:         {summary['extra_inline_images_downloaded']}")
        print(f"Failed assets:               {summary['failed_assets']}")
        print(f"Internal links rewritten:    {summary['internal_links_rewritten']}")
        print(f"Unresolved Zendesk links:    {summary['unresolved_zendesk_article_links']}")
        print(f"Restricted Zendesk articles: {summary['restricted_zendesk_articles']}")
        print(f"Draft articles:              {summary['draft_articles_exported']}")
        if zip_created:
            print(f"Confluence ZIP:              {summary['zip_path']}")
        else:
            print("Confluence ZIP:              NOT CREATED (blocking errors)")
        print("Reports:                     migration-report.csv / migration-report.json")

    def _download(self, url: str) -> tuple[bytes, str | None, str]:
        # Do not keep attachment/image bytes in memory across the migration. A large
        # knowledge base can contain gigabytes of media.
        return self._client.download(url)

    def _build_section_index_files(
        self,
        sections: list[dict[str, Any]],
        used_page_stems: set[str],
    ) -> dict[int, str]:
        result: dict[int, str] = {}
        for section in sections:
            section_id = self._required_int(section, "id")
            name = str(section.get("name") or f"Section {section_id}")
            stem = self._unique_page_stem(
                f"Section - {name}",
                fallback=f"Section {section_id}",
                suffix_hint=str(section_id),
                used_stems=used_page_stems,
            )
            result[section_id] = f"{stem}.html"
        return result

    def _unique_page_stem(
        self,
        value: str,
        *,
        fallback: str,
        suffix_hint: str,
        used_stems: set[str],
    ) -> str:
        base = self._safe_filename(value, fallback=fallback)
        candidate = base
        if candidate.casefold() in used_stems:
            candidate = self._safe_filename(
                f"{base} (Zendesk {suffix_hint})",
                fallback=f"{fallback} {suffix_hint}",
            )
        counter = 2
        while candidate.casefold() in used_stems:
            candidate = self._safe_filename(
                f"{base} (Zendesk {suffix_hint}-{counter})",
                fallback=f"{fallback} {suffix_hint}-{counter}",
            )
            counter += 1
        used_stems.add(candidate.casefold())
        return candidate

    def _article_sort_key(
        self,
        article: dict[str, Any],
        section_by_id: dict[int, dict[str, Any]],
    ) -> tuple[int, int, str]:
        section_id = self._as_int(article.get("section_id"))
        section = section_by_id.get(section_id) if section_id is not None else None
        section_position = self._as_int(section.get("position")) if section else None
        article_position = self._as_int(article.get("position"))
        return (
            section_position if section_position is not None else 1_000_000,
            article_position if article_position is not None else 1_000_000,
            str(article.get("title") or "").casefold(),
        )

    def _section_sort_key(self, section: dict[str, Any]) -> tuple[int, str]:
        position = self._as_int(section.get("position"))
        return (
            position if position is not None else 1_000_000,
            str(section.get("name") or "").casefold(),
        )

    def _unique_media_name(
        self,
        original_name: str,
        used_names: set[str],
        *,
        suffix_hint: str,
    ) -> str:
        safe = self._safe_filename(original_name, fallback=f"asset-{suffix_hint}", max_length=120)
        candidate = safe
        if candidate.casefold() not in used_names:
            used_names.add(candidate.casefold())
            return candidate

        path = Path(safe)
        base = path.stem
        suffix = path.suffix
        candidate = self._safe_filename(
            f"{base}-{suffix_hint}{suffix}",
            fallback=f"asset-{suffix_hint}{suffix}",
            max_length=120,
        )
        counter = 2
        while candidate.casefold() in used_names:
            candidate = self._safe_filename(
                f"{base}-{suffix_hint}-{counter}{suffix}",
                fallback=f"asset-{suffix_hint}-{counter}{suffix}",
                max_length=120,
            )
            counter += 1
        used_names.add(candidate.casefold())
        return candidate

    def _safe_filename(
        self,
        value: str,
        *,
        fallback: str,
        max_length: int = 140,
    ) -> str:
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
            digest = self._short_hash(cleaned)
            cleaned = f"{cleaned[: max_length - len(digest) - 3].rstrip()} - {digest}"
        return cleaned

    def _append_extension(self, file_name: str, content_type: str | None) -> str:
        mime = (content_type or "").split(";", 1)[0].strip().lower()
        overrides = {
            "image/jpeg": ".jpg",
            "image/svg+xml": ".svg",
            "image/webp": ".webp",
            "application/pdf": ".pdf",
        }
        extension = overrides.get(mime) or mimetypes.guess_extension(mime) or ""
        return f"{file_name}{extension}"

    def _filename_from_url(self, url: str) -> str:
        path = unquote(urlparse(url).path)
        return Path(path).name

    def _quoted_relative_media_path(self, page_stem: str, media_name: str) -> str:
        return quote(f"{page_stem}/{media_name}", safe="/")

    def _extract_article_id(self, url: str) -> int | None:
        path = urlparse(url).path
        match = _ARTICLE_ID_RE.search(path) or _API_ARTICLE_ID_RE.search(path)
        return int(match.group(1)) if match else None

    def _extract_attachment_id(self, url: str) -> int | None:
        match = _ATTACHMENT_ID_RE.search(urlparse(url).path)
        return int(match.group(1)) if match else None

    def _is_zendesk_host(self, url: str) -> bool:
        hostname = urlparse(url).hostname
        return bool(hostname and hostname.lower() == self._config.zendesk_host)

    def _normalize_url(self, url: str) -> str:
        parsed = urlparse(urljoin(self._config.zendesk_origin, url))
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

    def _short_hash(self, value: str) -> str:
        return hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]

    def _as_int(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _required_int(self, mapping: dict[str, Any], key: str) -> int:
        value = self._as_int(mapping.get(key))
        if value is None:
            raise ValueError(f"Expected integer field {key!r} in Zendesk response")
        return value
