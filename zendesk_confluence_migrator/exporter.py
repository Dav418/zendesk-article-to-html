from __future__ import annotations

import csv
import json
import logging
import mimetypes
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from zendesk_confluence_migrator.config import AppConfig
from zendesk_confluence_migrator.html_zip_renderer import HtmlZipRenderer
from zendesk_confluence_migrator.models import (
    ArticleRecord,
    AssetRecord,
    MigrationManifest,
    SectionRecord,
)
from zendesk_confluence_migrator.naming import (
    extract_attachment_id,
    filename_from_url,
    normalize_url,
    safe_filename,
    short_hash,
)
from zendesk_confluence_migrator.zendesk_client import ZendeskClient


class KnowledgeBaseExporter:
    def __init__(self, *, config: AppConfig, client: ZendeskClient) -> None:
        self._config = config
        self._client = client
        self._issues: list[dict[str, object]] = []
        self._asset_failures = 0

    def run(self) -> Path:
        logging.info("Reading Zendesk category, sections, and articles...")
        category = self._client.get_category()
        raw_sections = self._client.list_sections()
        raw_articles = self._client.list_articles()

        if not self._config.export.include_drafts:
            raw_articles = [article for article in raw_articles if not bool(article.get("draft"))]
        if not raw_articles:
            raise RuntimeError(
                "Zendesk returned zero articles for this category/locale. Check the category "
                "URL and the permissions of the authenticated Zendesk user."
            )

        category_name = str(category.get("name") or f"Category {self._config.zendesk.category_id}")
        safe_category_name = safe_filename(category_name, fallback="Zendesk Knowledge Base")
        workspace_dir = (
            self._config.export.output_dir.resolve()
            / f"zendesk-category-{self._config.zendesk.category_id}"
        )
        html_space_dir = workspace_dir / "html" / safe_category_name
        self._prepare_workspace(workspace_dir)
        html_space_dir.mkdir(parents=True, exist_ok=True)

        sections = self._build_sections(raw_sections)
        page_stems = self._build_article_stems(raw_articles)
        articles: list[ArticleRecord] = []
        article_rows: list[dict[str, object]] = []

        logging.info(
            "Found %d article(s) in %d section(s) for locale %s.",
            len(raw_articles),
            len(sections),
            self._config.zendesk.locale,
        )

        for index, raw_article in enumerate(self._sort_articles(raw_articles, raw_sections), start=1):
            article_id = self._required_int(raw_article, "id")
            title = str(raw_article.get("title") or f"Article {article_id}")
            logging.info("[%d/%d] Exporting %s", index, len(raw_articles), title)
            try:
                article = self._export_article(
                    raw_article=raw_article,
                    page_stem=page_stems[article_id],
                    workspace_dir=workspace_dir,
                    html_space_dir=html_space_dir,
                )
                articles.append(article)
                article_rows.append(
                    {
                        "article_id": article.id,
                        "title": article.title,
                        "section_id": article.section_id,
                        "position": article.position,
                        "draft": article.draft,
                        "restricted_in_zendesk": article.restricted,
                        "source_url": article.source_url,
                        "export_file": article.export_file,
                        "assets_downloaded": len(article.assets),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logging.exception("Failed to export Zendesk article %s", article_id)
                self._issues.append(
                    {
                        "severity": "error",
                        "type": "article_export_failed",
                        "article_id": article_id,
                        "title": title,
                        "url": raw_article.get("html_url"),
                        "detail": str(exc),
                    }
                )

        manifest = MigrationManifest(
            schema_version=1,
            generated_at_utc=datetime.now(timezone.utc).isoformat(),
            zendesk_origin=self._config.zendesk.origin,
            zendesk_host=self._config.zendesk.host,
            locale=self._config.zendesk.locale,
            category_id=self._config.zendesk.category_id,
            category_name=category_name,
            category_description_html=str(category.get("description") or ""),
            category_source_url=self._config.zendesk.category_url,
            html_space_folder=safe_category_name,
            sections=sections,
            articles=articles,
        )
        manifest_path = workspace_dir / "manifest.json"
        manifest.save(manifest_path)

        renderer = HtmlZipRenderer(manifest, workspace_dir)
        render_stats, render_issues = renderer.render_all()
        self._issues.extend(render_issues)
        for row in article_rows:
            stats = render_stats.get(int(row["article_id"]))
            row["internal_links_rewritten"] = stats.internal_links_rewritten if stats else 0
            row["unresolved_zendesk_links"] = stats.unresolved_zendesk_links if stats else 0

        report_csv = workspace_dir / "migration-report.csv"
        report_json = workspace_dir / "migration-report.json"
        zip_path = workspace_dir / f"{safe_category_name}-confluence-import.zip"
        self._write_csv(report_csv, article_rows)

        summary = self._summary(manifest, zip_path)
        report_json.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        blocking = any(issue.get("severity") == "error" for issue in self._issues)
        if self._config.export.fail_on_unresolved_zendesk_links:
            blocking = blocking or any(
                issue.get("type") == "unresolved_zendesk_link" for issue in self._issues
            )

        if blocking and not self._config.export.allow_partial_export:
            logging.error(
                "Export has blocking errors. The manifest/reports were written but the ZIP "
                "was not created. Review %s.",
                report_json,
            )
            self._print_summary(summary, zip_created=False)
            raise RuntimeError("Export contains blocking errors; see migration-report.json")

        self._create_zip(html_space_dir, zip_path)
        summary["zip_created"] = True
        summary["zip_size_bytes"] = zip_path.stat().st_size
        report_json.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._print_summary(summary, zip_created=True)
        return workspace_dir

    def _export_article(
        self,
        *,
        raw_article: dict[str, Any],
        page_stem: str,
        workspace_dir: Path,
        html_space_dir: Path,
    ) -> ArticleRecord:
        article_id = self._required_int(raw_article, "id")
        title = str(raw_article.get("title") or f"Article {article_id}")
        body_html = str(raw_article.get("body") or "")
        source_url = str(raw_article.get("html_url") or "") or None
        media_dir = html_space_dir / page_stem
        attachments = self._client.list_article_attachments(article_id)
        assets: list[AssetRecord] = []
        used_names: set[str] = set()

        for attachment in attachments:
            content_url = str(attachment.get("content_url") or "").strip()
            attachment_id = self._as_int(attachment.get("id"))
            if not content_url or attachment_id is None:
                continue
            file_name = self._unique_media_name(
                str(attachment.get("file_name") or f"attachment-{attachment_id}"),
                used_names,
                suffix_hint=str(attachment_id),
            )
            asset = self._download_asset(
                article_id=article_id,
                title=title,
                url=content_url,
                file_name=file_name,
                media_dir=media_dir,
                workspace_dir=workspace_dir,
                attachment_id=attachment_id,
                inline=bool(attachment.get("inline")),
            )
            if asset:
                assets.append(asset)

        existing_by_url: dict[str, AssetRecord] = {}
        existing_by_attachment_id: dict[int, AssetRecord] = {}
        for asset in assets:
            for source in asset.source_urls:
                existing_by_url[normalize_url(self._config.zendesk.origin, source)] = asset
            if asset.zendesk_attachment_id is not None:
                existing_by_attachment_id[asset.zendesk_attachment_id] = asset

        soup = BeautifulSoup(body_html, "html.parser")
        for index, image in enumerate(soup.find_all("img"), start=1):
            raw_src = image.get("src") or image.get("data-original") or image.get("data-src")
            if not raw_src or str(raw_src).startswith("data:"):
                continue
            absolute = urljoin(source_url or self._config.zendesk.origin, str(raw_src))
            normalized = normalize_url(self._config.zendesk.origin, absolute)
            attachment_id = extract_attachment_id(absolute)
            if normalized in existing_by_url:
                continue
            if attachment_id is not None and attachment_id in existing_by_attachment_id:
                continue

            proposed_name = filename_from_url(absolute) or f"image-{index}"
            proposed_name = self._unique_media_name(
                proposed_name,
                used_names,
                suffix_hint=short_hash(absolute),
            )
            asset = self._download_asset(
                article_id=article_id,
                title=title,
                url=absolute,
                file_name=proposed_name,
                media_dir=media_dir,
                workspace_dir=workspace_dir,
                attachment_id=None,
                inline=True,
            )
            if asset:
                assets.append(asset)
                for source in asset.source_urls:
                    existing_by_url[normalize_url(self._config.zendesk.origin, source)] = asset

        return ArticleRecord(
            id=article_id,
            title=title,
            body_html=body_html,
            source_url=source_url,
            section_id=self._as_int(raw_article.get("section_id")),
            position=self._as_int(raw_article.get("position")),
            draft=bool(raw_article.get("draft")),
            restricted=bool(raw_article.get("user_segment_id"))
            or bool(raw_article.get("user_segment_ids")),
            page_stem=page_stem,
            export_file=f"{page_stem}.html",
            assets=assets,
        )

    def _download_asset(
        self,
        *,
        article_id: int,
        title: str,
        url: str,
        file_name: str,
        media_dir: Path,
        workspace_dir: Path,
        attachment_id: int | None,
        inline: bool,
    ) -> AssetRecord | None:
        try:
            content, content_type, final_url = self._client.download(url)
            if "." not in Path(file_name).name:
                file_name = self._append_extension(file_name, content_type)
            media_dir.mkdir(parents=True, exist_ok=True)
            path = media_dir / file_name
            path.write_bytes(content)
            normalized_type = (content_type or "").split(";", 1)[0].strip().lower() or None
            is_image = bool(normalized_type and normalized_type.startswith("image/")) or Path(
                file_name
            ).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
            source_urls = list(dict.fromkeys([url, final_url]))
            return AssetRecord(
                file_name=file_name,
                relative_path=str(path.relative_to(workspace_dir)),
                source_urls=source_urls,
                content_type=normalized_type,
                zendesk_attachment_id=attachment_id,
                inline=inline,
                image=is_image,
            )
        except Exception as exc:  # noqa: BLE001
            self._asset_failures += 1
            self._issues.append(
                {
                    "severity": "error",
                    "type": "asset_download_failed",
                    "article_id": article_id,
                    "title": title,
                    "url": url,
                    "detail": str(exc),
                }
            )
            return None

    def _build_sections(self, raw_sections: list[dict[str, Any]]) -> list[SectionRecord]:
        result = []
        for raw in raw_sections:
            section_id = self._required_int(raw, "id")
            result.append(
                SectionRecord(
                    id=section_id,
                    name=str(raw.get("name") or f"Section {section_id}"),
                    description_html=str(raw.get("description") or ""),
                    source_url=(str(raw.get("html_url")) if raw.get("html_url") else None),
                    parent_section_id=self._as_int(raw.get("parent_section_id")),
                    position=self._as_int(raw.get("position")),
                )
            )
        return result

    def _build_article_stems(self, raw_articles: list[dict[str, Any]]) -> dict[int, str]:
        used: set[str] = set()
        result: dict[int, str] = {}
        for article in raw_articles:
            article_id = self._required_int(article, "id")
            title = str(article.get("title") or f"Article {article_id}")
            base = safe_filename(title, fallback=f"Article {article_id}")
            candidate = base
            if candidate.casefold() in used:
                candidate = safe_filename(
                    f"{base} (Zendesk {article_id})",
                    fallback=f"Article {article_id}",
                )
            serial = 2
            while candidate.casefold() in used:
                candidate = safe_filename(
                    f"{base} (Zendesk {article_id}-{serial})",
                    fallback=f"Article {article_id}-{serial}",
                )
                serial += 1
            used.add(candidate.casefold())
            result[article_id] = candidate
        return result

    def _sort_articles(
        self,
        raw_articles: list[dict[str, Any]],
        raw_sections: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        section_position = {
            self._required_int(section, "id"): self._as_int(section.get("position"))
            for section in raw_sections
        }
        return sorted(
            raw_articles,
            key=lambda article: (
                section_position.get(self._as_int(article.get("section_id")))
                if section_position.get(self._as_int(article.get("section_id"))) is not None
                else 1_000_000,
                self._as_int(article.get("position"))
                if self._as_int(article.get("position")) is not None
                else 1_000_000,
                str(article.get("title") or "").casefold(),
            ),
        )

    def _summary(self, manifest: MigrationManifest, zip_path: Path) -> dict[str, object]:
        return {
            "category": {
                "id": manifest.category_id,
                "name": manifest.category_name,
                "locale": manifest.locale,
                "source_url": manifest.category_source_url,
            },
            "articles_found": len(manifest.articles),
            "sections_found": len(manifest.sections),
            "draft_articles": sum(1 for article in manifest.articles if article.draft),
            "restricted_articles": sum(1 for article in manifest.articles if article.restricted),
            "assets_downloaded": sum(len(article.assets) for article in manifest.articles),
            "asset_failures": self._asset_failures,
            "unresolved_zendesk_links": sum(
                1 for issue in self._issues if issue.get("type") == "unresolved_zendesk_link"
            ),
            "issues": self._issues,
            "manifest_path": str((zip_path.parent / "manifest.json").resolve()),
            "zip_path": str(zip_path.resolve()),
            "zip_created": False,
        }

    def _write_csv(self, path: Path, rows: list[dict[str, object]]) -> None:
        columns = [
            "article_id",
            "title",
            "section_id",
            "position",
            "draft",
            "restricted_in_zendesk",
            "source_url",
            "export_file",
            "assets_downloaded",
            "internal_links_rewritten",
            "unresolved_zendesk_links",
        ]
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def _create_zip(self, html_space_dir: Path, zip_path: Path) -> None:
        if zip_path.exists():
            zip_path.unlink()
        created = shutil.make_archive(
            str(zip_path.with_suffix("")),
            "zip",
            root_dir=html_space_dir.parent,
            base_dir=html_space_dir.name,
        )
        created_path = Path(created)
        if created_path != zip_path:
            created_path.replace(zip_path)

    def _prepare_workspace(self, workspace_dir: Path) -> None:
        if workspace_dir.exists():
            state_path = workspace_dir / "confluence-upload-state.json"
            preserved_state = state_path.read_bytes() if state_path.exists() else None
            shutil.rmtree(workspace_dir)
            workspace_dir.mkdir(parents=True, exist_ok=True)
            if preserved_state is not None:
                state_path.write_bytes(preserved_state)
        else:
            workspace_dir.mkdir(parents=True, exist_ok=True)

    def _unique_media_name(self, value: str, used: set[str], *, suffix_hint: str) -> str:
        safe = safe_filename(value, fallback=f"asset-{suffix_hint}", max_length=120)
        candidate = safe
        if candidate.casefold() not in used:
            used.add(candidate.casefold())
            return candidate
        path = Path(safe)
        base = path.stem
        suffix = path.suffix
        candidate = safe_filename(
            f"{base}-{suffix_hint}{suffix}",
            fallback=f"asset-{suffix_hint}{suffix}",
            max_length=120,
        )
        serial = 2
        while candidate.casefold() in used:
            candidate = safe_filename(
                f"{base}-{suffix_hint}-{serial}{suffix}",
                fallback=f"asset-{suffix_hint}-{serial}{suffix}",
                max_length=120,
            )
            serial += 1
        used.add(candidate.casefold())
        return candidate

    def _append_extension(self, file_name: str, content_type: str | None) -> str:
        mime = (content_type or "").split(";", 1)[0].strip().lower()
        extension = {
            "image/jpeg": ".jpg",
            "image/svg+xml": ".svg",
            "image/webp": ".webp",
            "application/pdf": ".pdf",
        }.get(mime) or mimetypes.guess_extension(mime) or ""
        return f"{file_name}{extension}"

    def _print_summary(self, summary: dict[str, object], *, zip_created: bool) -> None:
        print()
        print("=== Zendesk export summary ===")
        print(f"Articles exported:           {summary['articles_found']}")
        print(f"Sections exported:           {summary['sections_found']}")
        print(f"Assets downloaded:           {summary['assets_downloaded']}")
        print(f"Asset failures:              {summary['asset_failures']}")
        print(f"Draft articles:              {summary['draft_articles']}")
        print(f"Restricted articles:         {summary['restricted_articles']}")
        print(f"Unresolved Zendesk links:    {summary['unresolved_zendesk_links']}")
        print(f"Manifest:                    {summary['manifest_path']}")
        print(
            f"Confluence HTML ZIP:         {summary['zip_path']}"
            if zip_created
            else "Confluence HTML ZIP:         NOT CREATED (blocking errors)"
        )

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
