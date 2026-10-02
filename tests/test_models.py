from pathlib import Path

from zendesk_confluence_migrator.models import ArticleRecord, MigrationManifest


def test_manifest_round_trip(tmp_path: Path):
    manifest = MigrationManifest(
        schema_version=1,
        generated_at_utc="2026-10-02T00:00:00+00:00",
        zendesk_origin="https://company.zendesk.com",
        zendesk_host="company.zendesk.com",
        locale="en-gb",
        category_id=123,
        category_name="Test",
        category_description_html="",
        category_source_url="https://company.zendesk.com/hc/en-gb/categories/123-test",
        html_space_folder="Test",
        sections=[],
        articles=[
            ArticleRecord(
                id=1,
                title="One",
                body_html="<p>Hello</p>",
                source_url=None,
                section_id=None,
                position=None,
                draft=False,
                restricted=False,
                page_stem="One",
                export_file="One.html",
                assets=[],
            )
        ],
    )
    path = tmp_path / "manifest.json"
    manifest.save(path)
    loaded = MigrationManifest.load(path)
    assert loaded.category_id == 123
    assert loaded.articles[0].title == "One"
