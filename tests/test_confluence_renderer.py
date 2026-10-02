from zendesk_confluence_migrator.confluence_renderer import ConfluenceStorageRenderer
from zendesk_confluence_migrator.models import ArticleRecord, AssetRecord, MigrationManifest, SectionRecord


def _manifest() -> MigrationManifest:
    return MigrationManifest(
        schema_version=1,
        generated_at_utc="2026-10-02T00:00:00+00:00",
        zendesk_origin="https://company.zendesk.com",
        zendesk_host="company.zendesk.com",
        locale="en-gb",
        category_id=100,
        category_name="Knowledge",
        category_description_html="",
        category_source_url="https://company.zendesk.com/hc/en-gb/categories/100-knowledge",
        html_space_folder="Knowledge",
        sections=[
            SectionRecord(
                id=10,
                name="Section",
                description_html="",
                source_url="https://company.zendesk.com/hc/en-gb/sections/10-section",
                parent_section_id=None,
                position=1,
            )
        ],
        articles=[],
    )


def test_renderer_rewrites_internal_article_link_and_image_attachment():
    manifest = _manifest()
    article = ArticleRecord(
        id=1,
        title="Source",
        body_html=(
            '<p><a href="https://company.zendesk.com/hc/en-gb/articles/2-target">Target</a></p>'
            '<p><img src="https://company.zendesk.com/hc/article_attachments/99/pic.png" alt="Pic"></p>'
        ),
        source_url="https://company.zendesk.com/hc/en-gb/articles/1-source",
        section_id=10,
        position=1,
        draft=False,
        restricted=False,
        page_stem="Source",
        export_file="Source.html",
        assets=[
            AssetRecord(
                file_name="pic.png",
                relative_path="html/Knowledge/Source/pic.png",
                source_urls=["https://company.zendesk.com/hc/article_attachments/99/pic.png"],
                content_type="image/png",
                zendesk_attachment_id=99,
                inline=True,
                image=True,
            )
        ],
    )

    body, stats, issues = ConfluenceStorageRenderer(manifest).render_article(
        article=article,
        article_urls={1: "https://company.atlassian.net/wiki/spaces/KB/pages/1", 2: "https://company.atlassian.net/wiki/spaces/KB/pages/2"},
        section_urls={10: "https://company.atlassian.net/wiki/spaces/KB/pages/10"},
        category_url="https://company.atlassian.net/wiki/spaces/KB/pages/100",
    )

    assert 'href="https://company.atlassian.net/wiki/spaces/KB/pages/2"' in body
    assert "<ac:image" in body
    assert 'ri:filename="pic.png"' in body
    assert stats.internal_links_rewritten == 1
    assert stats.images_rewritten == 1
    assert not issues
