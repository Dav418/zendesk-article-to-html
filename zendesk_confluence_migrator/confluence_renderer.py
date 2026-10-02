from __future__ import annotations

import html
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, CData, Comment

from zendesk_confluence_migrator.models import ArticleRecord, MigrationManifest, SectionRecord
from zendesk_confluence_migrator.naming import (
    extract_article_id,
    extract_attachment_id,
    extract_category_id,
    extract_section_id,
    normalize_url,
)


@dataclass(frozen=True)
class ConfluenceRenderStats:
    internal_links_rewritten: int
    unresolved_zendesk_links: int
    attachment_links_rewritten: int
    images_rewritten: int


class ConfluenceStorageRenderer:
    def __init__(self, manifest: MigrationManifest) -> None:
        self._manifest = manifest

    def render_article(
        self,
        *,
        article: ArticleRecord,
        article_urls: dict[int, str],
        section_urls: dict[int, str],
        category_url: str,
    ) -> tuple[str, ConfluenceRenderStats, list[dict[str, object]]]:
        soup = BeautifulSoup(article.body_html, "html.parser")
        for unwanted in soup.find_all(["script", "style"]):
            unwanted.decompose()

        asset_by_url, asset_by_attachment_id = self._asset_maps(article)
        internal_rewritten = 0
        unresolved = 0
        attachment_rewritten = 0
        images_rewritten = 0
        issues: list[dict[str, object]] = []
        referenced_asset_names: set[str] = set()

        for iframe in soup.find_all("iframe"):
            src = str(iframe.get("src") or "").strip()
            if src:
                replacement = soup.new_tag("p")
                link = soup.new_tag("a", href=urljoin(article.source_url or self._manifest.zendesk_origin, src))
                link.string = "Embedded content"
                replacement.append(link)
                iframe.replace_with(replacement)
            else:
                iframe.decompose()

        for image_tag in list(soup.find_all("img")):
            raw_src = image_tag.get("src") or image_tag.get("data-original") or image_tag.get("data-src")
            if not raw_src:
                continue
            absolute = urljoin(article.source_url or self._manifest.zendesk_origin, str(raw_src))
            asset = self._find_asset(asset_by_url, asset_by_attachment_id, absolute)
            if asset is None:
                if self._is_zendesk_host(absolute):
                    issues.append(self._unresolved_issue(article, absolute, "image"))
                    unresolved += 1
                continue

            ac_image = soup.new_tag("ac:image")
            alt = str(image_tag.get("alt") or "").strip()
            if alt:
                ac_image["ac:alt"] = alt
            width = str(image_tag.get("width") or "").strip()
            if width.isdigit():
                ac_image["ac:width"] = width
            ri_attachment = soup.new_tag("ri:attachment")
            ri_attachment["ri:filename"] = asset.file_name
            ac_image.append(ri_attachment)
            image_tag.replace_with(ac_image)
            referenced_asset_names.add(asset.file_name)
            images_rewritten += 1

        for anchor in list(soup.find_all("a")):
            raw_href = anchor.get("href")
            if not raw_href:
                continue
            href = str(raw_href).strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            absolute = urljoin(article.source_url or self._manifest.zendesk_origin, href)
            fragment = urlparse(absolute).fragment

            asset = self._find_asset(asset_by_url, asset_by_attachment_id, absolute)
            if asset is not None:
                self._replace_anchor_with_attachment(soup, anchor, asset.file_name)
                referenced_asset_names.add(asset.file_name)
                attachment_rewritten += 1
                continue

            linked_article_id = extract_article_id(absolute)
            if linked_article_id is not None:
                target = article_urls.get(linked_article_id)
                if target:
                    anchor["href"] = target + (f"#{fragment}" if fragment else "")
                    internal_rewritten += 1
                elif self._is_zendesk_host(absolute):
                    unresolved += 1
                    issues.append(self._unresolved_issue(article, absolute, "article"))
                continue

            linked_section_id = extract_section_id(absolute)
            if linked_section_id is not None:
                target = section_urls.get(linked_section_id)
                if target:
                    anchor["href"] = target + (f"#{fragment}" if fragment else "")
                    internal_rewritten += 1
                elif self._is_zendesk_host(absolute):
                    unresolved += 1
                    issues.append(self._unresolved_issue(article, absolute, "section"))
                continue

            linked_category_id = extract_category_id(absolute)
            if linked_category_id == self._manifest.category_id:
                anchor["href"] = category_url + (f"#{fragment}" if fragment else "")
                internal_rewritten += 1

        remaining = [
            asset
            for asset in article.assets
            if not asset.inline and asset.file_name not in referenced_asset_names
        ]
        if remaining:
            heading = soup.new_tag("h2")
            heading.string = "Attachments"
            soup.append(heading)
            listing = soup.new_tag("ul")
            for asset in remaining:
                item = soup.new_tag("li")
                link = soup.new_tag("ac:link")
                ri_attachment = soup.new_tag("ri:attachment")
                ri_attachment["ri:filename"] = asset.file_name
                link.append(ri_attachment)
                link_body = soup.new_tag("ac:plain-text-link-body")
                link_body.append(CData(asset.file_name))
                link.append(link_body)
                item.append(link)
                listing.append(item)
            soup.append(listing)

        if article.draft:
            marker = soup.new_tag("p")
            strong = soup.new_tag("strong")
            strong.string = "Zendesk status:"
            marker.append(strong)
            marker.append(" Draft")
            soup.insert(0, marker)

        soup.append(Comment(f" migrated from Zendesk article {article.id} "))
        return (
            str(soup),
            ConfluenceRenderStats(
                internal_links_rewritten=internal_rewritten,
                unresolved_zendesk_links=unresolved,
                attachment_links_rewritten=attachment_rewritten,
                images_rewritten=images_rewritten,
            ),
            issues,
        )

    def render_section(
        self,
        *,
        section: SectionRecord,
        child_sections: list[SectionRecord],
        articles: list[ArticleRecord],
        section_urls: dict[int, str],
        article_urls: dict[int, str],
        category_url: str,
    ) -> str:
        parts: list[str] = []
        if section.description_html.strip():
            parts.append(
                self._rewrite_fragment_links(
                    section.description_html,
                    article_urls=article_urls,
                    section_urls=section_urls,
                    category_url=category_url,
                )
            )
        if child_sections:
            parts.append("<h2>Sections</h2><ul>")
            for child in sorted(child_sections, key=self._section_sort_key):
                parts.append(
                    f'<li><a href="{html.escape(section_urls[child.id], quote=True)}">'
                    f"{html.escape(child.name)}</a></li>"
                )
            parts.append("</ul>")
        if articles:
            parts.append("<h2>Articles</h2><ol>")
            for article in sorted(articles, key=self._article_sort_key):
                parts.append(
                    f'<li><a href="{html.escape(article_urls[article.id], quote=True)}">'
                    f"{html.escape(article.title)}</a>{' [Draft]' if article.draft else ''}</li>"
                )
            parts.append("</ol>")
        parts.append(f"<!-- migrated from Zendesk section {section.id} -->")
        return "".join(parts)

    def render_category(
        self,
        *,
        top_level_sections: list[SectionRecord],
        section_urls: dict[int, str],
        article_urls: dict[int, str],
        category_url: str,
    ) -> str:
        parts: list[str] = []
        if self._manifest.category_description_html.strip():
            parts.append(
                self._rewrite_fragment_links(
                    self._manifest.category_description_html,
                    article_urls=article_urls,
                    section_urls=section_urls,
                    category_url=category_url,
                )
            )
        if top_level_sections:
            parts.append("<h2>Sections</h2><ul>")
            for section in sorted(top_level_sections, key=self._section_sort_key):
                parts.append(
                    f'<li><a href="{html.escape(section_urls[section.id], quote=True)}">'
                    f"{html.escape(section.name)}</a></li>"
                )
            parts.append("</ul>")
        parts.append(f"<!-- migrated from Zendesk category {self._manifest.category_id} -->")
        return "".join(parts)


    def _rewrite_fragment_links(
        self,
        fragment: str,
        *,
        article_urls: dict[int, str],
        section_urls: dict[int, str],
        category_url: str,
    ) -> str:
        soup = BeautifulSoup(fragment, "html.parser")
        for anchor in soup.find_all("a"):
            raw_href = anchor.get("href")
            if not raw_href:
                continue
            href = str(raw_href).strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            absolute = urljoin(self._manifest.zendesk_origin, href)
            fragment_id = urlparse(absolute).fragment
            article_id = extract_article_id(absolute)
            if article_id is not None and article_id in article_urls:
                anchor["href"] = article_urls[article_id] + (f"#{fragment_id}" if fragment_id else "")
                continue
            section_id = extract_section_id(absolute)
            if section_id is not None and section_id in section_urls:
                anchor["href"] = section_urls[section_id] + (f"#{fragment_id}" if fragment_id else "")
                continue
            category_id = extract_category_id(absolute)
            if category_id == self._manifest.category_id:
                anchor["href"] = category_url + (f"#{fragment_id}" if fragment_id else "")
        return str(soup)

    def _asset_maps(self, article: ArticleRecord):
        by_url = {}
        by_attachment_id = {}
        for asset in article.assets:
            for source_url in asset.source_urls:
                by_url[normalize_url(self._manifest.zendesk_origin, source_url)] = asset
            if asset.zendesk_attachment_id is not None:
                by_attachment_id[asset.zendesk_attachment_id] = asset
        return by_url, by_attachment_id

    def _find_asset(self, by_url, by_attachment_id, absolute_url: str):
        asset = by_url.get(normalize_url(self._manifest.zendesk_origin, absolute_url))
        if asset is not None:
            return asset
        attachment_id = extract_attachment_id(absolute_url)
        return by_attachment_id.get(attachment_id) if attachment_id is not None else None

    def _replace_anchor_with_attachment(self, soup, anchor, file_name: str) -> None:
        text = anchor.get_text(" ", strip=True) or file_name
        link = soup.new_tag("ac:link")
        ri_attachment = soup.new_tag("ri:attachment")
        ri_attachment["ri:filename"] = file_name
        link.append(ri_attachment)
        link_body = soup.new_tag("ac:plain-text-link-body")
        link_body.append(CData(text))
        link.append(link_body)
        anchor.replace_with(link)

    def _is_zendesk_host(self, url: str) -> bool:
        host = urlparse(url).hostname
        return bool(host and host.lower() == self._manifest.zendesk_host)

    def _unresolved_issue(self, article: ArticleRecord, url: str, target_type: str) -> dict[str, object]:
        return {
            "severity": "warning",
            "type": "unresolved_zendesk_link",
            "article_id": article.id,
            "title": article.title,
            "url": url,
            "detail": f"Linked Zendesk {target_type} is outside this migration or inaccessible.",
        }

    def _section_sort_key(self, section: SectionRecord) -> tuple[int, str]:
        return (section.position if section.position is not None else 1_000_000, section.name.casefold())

    def _article_sort_key(self, article: ArticleRecord) -> tuple[int, str]:
        return (article.position if article.position is not None else 1_000_000, article.title.casefold())
