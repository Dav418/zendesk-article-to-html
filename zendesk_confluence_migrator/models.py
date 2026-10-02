from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AssetRecord:
    file_name: str
    relative_path: str
    source_urls: list[str]
    content_type: str | None = None
    zendesk_attachment_id: int | None = None
    inline: bool = False
    image: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AssetRecord":
        return cls(
            file_name=str(data["file_name"]),
            relative_path=str(data["relative_path"]),
            source_urls=[str(value) for value in data.get("source_urls", [])],
            content_type=(str(data["content_type"]) if data.get("content_type") else None),
            zendesk_attachment_id=(
                int(data["zendesk_attachment_id"])
                if data.get("zendesk_attachment_id") is not None
                else None
            ),
            inline=bool(data.get("inline", False)),
            image=bool(data.get("image", False)),
        )


@dataclass(frozen=True)
class SectionRecord:
    id: int
    name: str
    description_html: str
    source_url: str | None
    parent_section_id: int | None
    position: int | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SectionRecord":
        return cls(
            id=int(data["id"]),
            name=str(data["name"]),
            description_html=str(data.get("description_html") or ""),
            source_url=(str(data["source_url"]) if data.get("source_url") else None),
            parent_section_id=(
                int(data["parent_section_id"])
                if data.get("parent_section_id") is not None
                else None
            ),
            position=(int(data["position"]) if data.get("position") is not None else None),
        )


@dataclass(frozen=True)
class ArticleRecord:
    id: int
    title: str
    body_html: str
    source_url: str | None
    section_id: int | None
    position: int | None
    draft: bool
    restricted: bool
    page_stem: str
    export_file: str
    assets: list[AssetRecord] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArticleRecord":
        return cls(
            id=int(data["id"]),
            title=str(data["title"]),
            body_html=str(data.get("body_html") or ""),
            source_url=(str(data["source_url"]) if data.get("source_url") else None),
            section_id=(int(data["section_id"]) if data.get("section_id") is not None else None),
            position=(int(data["position"]) if data.get("position") is not None else None),
            draft=bool(data.get("draft", False)),
            restricted=bool(data.get("restricted", False)),
            page_stem=str(data["page_stem"]),
            export_file=str(data["export_file"]),
            assets=[AssetRecord.from_dict(item) for item in data.get("assets", [])],
        )


@dataclass(frozen=True)
class MigrationManifest:
    schema_version: int
    generated_at_utc: str
    zendesk_origin: str
    zendesk_host: str
    locale: str
    category_id: int
    category_name: str
    category_description_html: str
    category_source_url: str
    html_space_folder: str
    sections: list[SectionRecord]
    articles: list[ArticleRecord]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "MigrationManifest":
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("schema_version", 0)) != 1:
            raise ValueError(
                f"Unsupported manifest schema version: {data.get('schema_version')!r}"
            )
        return cls(
            schema_version=1,
            generated_at_utc=str(data["generated_at_utc"]),
            zendesk_origin=str(data["zendesk_origin"]),
            zendesk_host=str(data["zendesk_host"]),
            locale=str(data["locale"]),
            category_id=int(data["category_id"]),
            category_name=str(data["category_name"]),
            category_description_html=str(data.get("category_description_html") or ""),
            category_source_url=str(data["category_source_url"]),
            html_space_folder=str(data["html_space_folder"]),
            sections=[SectionRecord.from_dict(item) for item in data.get("sections", [])],
            articles=[ArticleRecord.from_dict(item) for item in data.get("articles", [])],
        )


@dataclass
class ConfluencePageState:
    page_id: str
    title: str
    url: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConfluencePageState":
        return cls(
            page_id=str(data["page_id"]),
            title=str(data["title"]),
            url=str(data["url"]),
        )


@dataclass
class UploadState:
    schema_version: int
    category_id: int
    confluence_base_url: str
    confluence_space_key: str
    configured_parent_page_id: str
    create_category_root: bool
    root: ConfluencePageState | None = None
    sections: dict[int, ConfluencePageState] = field(default_factory=dict)
    articles: dict[int, ConfluencePageState] = field(default_factory=dict)
    planned_section_titles: dict[int, str] = field(default_factory=dict)
    planned_article_titles: dict[int, str] = field(default_factory=dict)
    planned_root_title: str | None = None

    def save(self, path: Path) -> None:
        payload = {
            "schema_version": self.schema_version,
            "category_id": self.category_id,
            "confluence_base_url": self.confluence_base_url,
            "confluence_space_key": self.confluence_space_key,
            "configured_parent_page_id": self.configured_parent_page_id,
            "create_category_root": self.create_category_root,
            "root": asdict(self.root) if self.root else None,
            "sections": {str(key): asdict(value) for key, value in self.sections.items()},
            "articles": {str(key): asdict(value) for key, value in self.articles.items()},
            "planned_section_titles": {
                str(key): value for key, value in self.planned_section_titles.items()
            },
            "planned_article_titles": {
                str(key): value for key, value in self.planned_article_titles.items()
            },
            "planned_root_title": self.planned_root_title,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "UploadState":
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("schema_version", 0)) != 1:
            raise ValueError(
                f"Unsupported upload state schema version: {data.get('schema_version')!r}"
            )
        return cls(
            schema_version=1,
            category_id=int(data["category_id"]),
            confluence_base_url=str(data["confluence_base_url"]),
            confluence_space_key=str(data["confluence_space_key"]),
            configured_parent_page_id=str(data["configured_parent_page_id"]),
            create_category_root=bool(data["create_category_root"]),
            root=(ConfluencePageState.from_dict(data["root"]) if data.get("root") else None),
            sections={
                int(key): ConfluencePageState.from_dict(value)
                for key, value in data.get("sections", {}).items()
            },
            articles={
                int(key): ConfluencePageState.from_dict(value)
                for key, value in data.get("articles", {}).items()
            },
            planned_section_titles={
                int(key): str(value)
                for key, value in data.get("planned_section_titles", {}).items()
            },
            planned_article_titles={
                int(key): str(value)
                for key, value in data.get("planned_article_titles", {}).items()
            },
            planned_root_title=(
                str(data["planned_root_title"])
                if data.get("planned_root_title") is not None
                else None
            ),
        )
