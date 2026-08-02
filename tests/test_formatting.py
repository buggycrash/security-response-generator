from security_response_generator.generation.formatting import normalize_to_ascii


def test_replaces_smart_quotes_dashes_and_ellipsis():
    text = "“Hello” — it’s a test…"
    assert normalize_to_ascii(text) == '"Hello" - it\'s a test...'


def test_replaces_bullet_characters():
    text = "• first\n• second"
    assert normalize_to_ascii(text) == "- first\n- second"


def test_strips_markdown_headers():
    text = "## SI-5 Response\n\nBody text."
    assert normalize_to_ascii(text) == "SI-5 Response\n\nBody text."


def test_strips_bold_and_italic_markers():
    text = "This is **important** and *emphasized* and __also bold__."
    assert normalize_to_ascii(text) == "This is important and emphasized and also bold."


def test_strips_inline_code_and_fences():
    text = "Run `srg ingest` inside:\n```\nsome code\n```\ndone."
    result = normalize_to_ascii(text)
    assert "`" not in result
    assert "srg ingest" in result
    assert "some code" in result


def test_converts_markdown_links_to_plain_text():
    text = "See [the docs](https://example.com) for details."
    assert normalize_to_ascii(text) == "See the docs (https://example.com) for details."


def test_drops_remaining_non_ascii_characters():
    text = "Emoji test \U0001f600 done."
    result = normalize_to_ascii(text)
    assert all(ord(c) < 128 for c in result)
    assert "Emoji test" in result
    assert "done." in result


def test_idempotent_on_already_plain_text():
    text = "This is already plain ASCII text.\n\nSecond paragraph."
    assert normalize_to_ascii(text) == text


def test_collapses_excess_blank_lines_left_by_stripped_headers():
    text = "# Heading\n\n\n\nBody."
    assert normalize_to_ascii(text) == "Heading\n\nBody."
