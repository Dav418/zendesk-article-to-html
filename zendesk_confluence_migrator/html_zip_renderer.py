from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from zendesk_confluence_migrator.models import ArticleRecord, MigrationManifest, SectionRecord
from zendesk_confluence_migrator.naming import (
    extract_article_id,
    extract_attachment_id,
    extract_category_id,
    extract_section_id,
    normalize_url,
    safe_filename,
)


@dataclass(frozen=True)
class RenderStats:
    internal_links_rewritten: int = 0
    unresolved_zendesk_links: int = 0


class HtmlZipRenderer:
    def __init__(self, manifest: MigrationManifest, workspace_dir: Path) -> None:
        self._manifest = manifest
        self._workspace_dir = workspace_dir
        self._articles_by_id = {article.id: article for article in manifest.articles}
        self._sections_by_id = {section.id: section for section in manifest.sections}
        self._section_file_by_id = self._build_section_file_map()
        used_files = {article.export_file.casefold() for article in manifest.articles}
        used_files.update(value.casefold() for value in self._section_file_by_id.values())
        category_base = safe_filename(
            f"{manifest.category_name} - Index", fallback="Knowledge Base - Index"
        )
        category_file = f"{category_base}.html"
        if category_file.casefold() in used_files:
            category_file = f"{category_base} (Zendesk {manifest.category_id}).html"
        serial = 2
        while category_file.casefold() in used_files:
            category_file = (
                f"{category_base} (Zendesk {manifest.category_id}-{serial}).html"
            )
            serial += 1
        self._category_index_file = category_file

    @property
    def section_file_by_id(self) -> dict[int, str]:
        return dict(self._section_file_by_id)

    @property
    def category_index_file(self) -> str:
        return self._category_index_file

    def render_all(self) -> tuple[dict[int, RenderStats], list[dict[str, object]]]:
        html_space_dir = self._workspace_dir / "html" / self._manifest.html_space_folder
        html_space_dir.mkdir(parents=True, exist_ok=True)
        issues: list[dict[str, object]] = []
        stats_by_article: dict[int, RenderStats] = {}

        for article in self._manifest.articles:
            stats, article_issues = self._render_article(article, html_space_dir)
            stats_by_article[article.id] = stats
            issues.extend(article_issues)

        self._render_section_pages(html_space_dir)
        self._render_category_page(html_space_dir)
        return stats_by_article, issues

    def _render_article(
        self,
        article: ArticleRecord,
        html_space_dir: Path,
    ) -> tuple[RenderStats, list[dict[str, object]]]:
        soup = BeautifulSoup(article.body_html, "html.parser")
        asset_by_url, asset_by_attachment_id = self._asset_maps(article)
        internal_links_rewritten = 0
        unresolved = 0
        issues: list[dict[str, object]] = []

        for image_tag in soup.find_all("img"):
            raw_src = image_tag.get("src") or image_tag.get("data-original") or image_tag.get("data-src")
            if not raw_src:
                continue
            absolute = urljoin(article.source_url or self._manifest.zendesk_origin, str(raw_src))
            asset = asset_by_url.get(normalize_url(self._manifest.zendesk_origin, absolute))
            attachment_id = extract_attachment_id(absolute)
            if asset is None and attachment_id is not None:
                asset = asset_by_attachment_id.get(attachment_id)
            if asset is not None:
                image_tag["src"] = quote(f"{article.page_stem}/{asset.file_name}", safe="/")
                image_tag.attrs.pop("srcset", None)
                image_tag.attrs.pop("data-original", None)
                image_tag.attrs.pop("data-src", None)

        for anchor in soup.find_all("a"):
            raw_href = anchor.get("href")
            if not raw_href:
                continue
            href = str(raw_href).strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            absolute = urljoin(article.source_url or self._manifest.zendesk_origin, href)

            asset = asset_by_url.get(normalize_url(self._manifest.zendesk_origin, absolute))
            attachment_id = extract_attachment_id(absolute)
            if asset is None and attachment_id is not None:
                asset = asset_by_attachment_id.get(attachment_id)
            if asset is not None:
                anchor["href"] = quote(f"{article.page_stem}/{asset.file_name}", safe="/")
                continue

            fragment = urlparse(absolute).fragment
            linked_article_id = extract_article_id(absolute)
            if linked_article_id is not None:
                target = self._articles_by_id.get(linked_article_id)
                if target is not None:
                    anchor["href"] = quote(target.export_file) + (f"#{fragment}" if fragment else "")
                    internal_links_rewritten += 1
                elif self._is_zendesk_host(absolute):
                    unresolved += 1
                    issues.append(self._unresolved_issue(article, absolute, "article"))
                continue

            linked_section_id = extract_section_id(absolute)
            if linked_section_id is not None:
                target_file = self._section_file_by_id.get(linked_section_id)
                if target_file:
                    anchor["href"] = quote(target_file) + (f"#{fragment}" if fragment else "")
                    internal_links_rewritten += 1
                elif self._is_zendesk_host(absolute):
                    unresolved += 1
                    issues.append(self._unresolved_issue(article, absolute, "section"))
                continue

            linked_category_id = extract_category_id(absolute)
            if linked_category_id == self._manifest.category_id:
                anchor["href"] = quote(self._category_index_file) + (f"#{fragment}" if fragment else "")
                internal_links_rewritten += 1

        linked_asset_names = {
            self._asset_name_for_href(article, str(anchor.get("href") or ""))
            for anchor in soup.find_all("a")
        }
        remaining_attachments = [
            asset
            for asset in article.assets
            if not asset.inline and asset.file_name not in linked_asset_names
        ]
        if remaining_attachments:
            heading = soup.new_tag("h2")
            heading.string = "Attachments"
            soup.append(heading)
            listing = soup.new_tag("ul")
            for asset in remaining_attachments:
                item = soup.new_tag("li")
                link = soup.new_tag("a")
                link["href"] = quote(f"{article.page_stem}/{asset.file_name}", safe="/")
                link.string = asset.file_name
                item.append(link)
                listing.append(item)
            soup.append(listing)

        wrapper = BeautifulSoup("<!doctype html><html><head></head><body></body></html>", "html.parser")
        wrapper.head.append(wrapper.new_tag("meta", charset="utf-8"))
        section_name = self._section_name(article.section_id)
        marker = wrapper.new_tag("p")
        strong = wrapper.new_tag("strong")
        strong.string = "Zendesk section:"
        marker.append(strong)
        marker.append(f" {section_name}")
        wrapper.body.append(marker)
        if article.draft:
            draft = wrapper.new_tag("p")
            strong = wrapper.new_tag("strong")
            strong.string = "Zendesk status:"
            draft.append(strong)
            draft.append(" Draft")
            wrapper.body.append(draft)
        wrapper.body.append(wrapper.new_tag("hr"))
        for node in list(soup.contents):
            wrapper.body.append(node)

        (html_space_dir / article.export_file).write_text(str(wrapper), encoding="utf-8")
        return (
            RenderStats(
                internal_links_rewritten=internal_links_rewritten,
                unresolved_zendesk_links=unresolved,
            ),
            issues,
        )

    def _render_section_pages(self, html_space_dir: Path) -> None:
        children_by_parent: dict[int | None, list[SectionRecord]] = {}
        for section in self._manifest.sections:
            children_by_parent.setdefault(section.parent_section_id, []).append(section)
        articles_by_section: dict[int | None, list[ArticleRecord]] = {}
        for article in self._manifest.articles:
            articles_by_section.setdefault(article.section_id, []).append(article)

        for section in self._manifest.sections:
            body: list[str] = [f"<h1>{html.escape(section.name)}</h1>"]
            if section.description_html.strip():
                body.append(section.description_html)

            child_sections = sorted(children_by_parent.get(section.id, []), key=self._section_sort_key)
            if child_sections:
                body.append("<h2>Sections</h2><ul>")
                for child in child_sections:
                    body.append(
                        f'<li><a href="{quote(self._section_file_by_id[child.id])}">' 
                        f"{html.escape(child.name)}</a></li>"
                    )
                body.append("</ul>")

            articles = sorted(articles_by_section.get(section.id, []), key=self._article_sort_key)
            if articles:
                body.append("<h2>Articles</h2><ol>")
                for article in articles:
                    suffix = " [Draft]" if article.draft else ""
                    body.append(
                        f'<li><a href="{quote(article.export_file)}">'
                        f"{html.escape(article.title)}</a>{suffix}</li>"
                    )
                body.append("</ol>")

            page = self._html_document("".join(body))
            (html_space_dir / self._section_file_by_id[section.id]).write_text(
                page, encoding="utf-8"
            )

    def _render_category_page(self, html_space_dir: Path) -> None:
        body = [f"<h1>{html.escape(self._manifest.category_name)}</h1>"]
        if self._manifest.category_description_html.strip():
            body.append(self._manifest.category_description_html)
        body.append("<h2>Sections</h2><ul>")
        section_ids = {section.id for section in self._manifest.sections}
        top_level = [
            section
            for section in self._manifest.sections
            if section.parent_section_id is None or section.parent_section_id not in section_ids
        ]
        for section in sorted(top_level, key=self._section_sort_key):
            body.append(
                f'<li><a href="{quote(self._section_file_by_id[section.id])}">'
                f"{html.escape(section.name)}</a></li>"
            )
        body.append("</ul>")
        (html_space_dir / self._category_index_file).write_text(
            self._html_document("".join(body)), encoding="utf-8"
        )

    def _build_section_file_map(self) -> dict[int, str]:
        used = {article.export_file.casefold() for article in self._manifest.articles}
        result: dict[int, str] = {}
        for section in sorted(self._manifest.sections, key=self._section_sort_key):
            base = safe_filename(f"Section - {section.name}", fallback=f"Section {section.id}")
            candidate = f"{base}.html"
            if candidate.casefold() in used:
                candidate = f"{base} (Zendesk {section.id}).html"
            serial = 2
            while candidate.casefold() in used:
                candidate = f"{base} (Zendesk {section.id}-{serial}).html"
                serial += 1
            used.add(candidate.casefold())
            result[section.id] = candidate
        return result

    def _asset_maps(self, article: ArticleRecord):
        by_url = {}
        by_attachment_id = {}
        for asset in article.assets:
            for source_url in asset.source_urls:
                by_url[normalize_url(self._manifest.zendesk_origin, source_url)] = asset
            if asset.zendesk_attachment_id is not None:
                by_attachment_id[asset.zendesk_attachment_id] = asset
        return by_url, by_attachment_id

    def _asset_name_for_href(self, article: ArticleRecord, href: str) -> str | None:
        if not href:
            return None
        prefix = quote(f"{article.page_stem}/", safe="/")
        if href.startswith(prefix):
            return unquote(href[len(prefix):])
        return None

    def _section_name(self, section_id: int | None) -> str:
        if section_id is None:
            return "No section"
        section = self._sections_by_id.get(section_id)
        return section.name if section else f"Section {section_id}"

    def _unresolved_issue(self, article: ArticleRecord, url: str, target_type: str) -> dict[str, object]:
        return {
            "severity": "warning",
            "type": "unresolved_zendesk_link",
            "article_id": article.id,
            "title": article.title,
            "url": url,
            "detail": f"Linked Zendesk {target_type} is outside this export or inaccessible.",
        }

    def _is_zendesk_host(self, url: str) -> bool:
        host = urlparse(url).hostname
        return bool(host and host.lower() == self._manifest.zendesk_host)

    def _section_sort_key(self, section: SectionRecord) -> tuple[int, str]:
        return (section.position if section.position is not None else 1_000_000, section.name.casefold())

    def _article_sort_key(self, article: ArticleRecord) -> tuple[int, str]:
        return (article.position if article.position is not None else 1_000_000, article.title.casefold())

    def _html_document(self, body: str) -> str:
        return f'<!doctype html><html><head><meta charset="utf-8"></head><body>{body}</body></html>'
