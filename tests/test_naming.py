from zendesk_confluence_migrator.naming import bounded_title, unique_title


def test_unique_title_keeps_original_when_free():
    used = set()
    assert unique_title("A page", suffix_hint="article-1", used_casefolded=used) == "A page"


def test_unique_title_adds_zendesk_suffix_when_taken():
    used = {"a page"}
    result = unique_title("A page", suffix_hint="article-123", used_casefolded=used)
    assert result == "A page (Zendesk article-123)"


def test_bounded_title_limits_length():
    result = bounded_title("x" * 400, fallback="fallback")
    assert len(result) <= 250
