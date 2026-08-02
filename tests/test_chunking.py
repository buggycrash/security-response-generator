from security_response_generator.ingest.chunking import chunk_text


def test_short_text_fits_in_single_chunk():
    text = "This is a short paragraph about SI-5."
    chunks = chunk_text(text, chunk_size=1000, overlap=100)

    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].chunk_index == 0


def test_long_text_splits_into_multiple_chunks_with_overlap():
    paragraphs = [f"Paragraph {i} " + ("word " * 50) for i in range(10)]
    text = "\n\n".join(paragraphs)

    chunks = chunk_text(text, chunk_size=300, overlap=50)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.text) <= 300 + 50  # allow overlap slack from paragraph joins


def test_oversized_single_paragraph_uses_sliding_window():
    text = "x" * 1000
    chunks = chunk_text(text, chunk_size=300, overlap=50)

    assert len(chunks) > 1
    # Sliding window chunks should all respect the requested size.
    for chunk in chunks:
        assert len(chunk.text) <= 300


def test_control_ids_are_tagged():
    text = "This section discusses SI-5 and AC-2(1) in detail."
    chunks = chunk_text(text, chunk_size=1000, overlap=100)

    assert chunks[0].control_ids == ["AC-2(1)", "SI-5"]


def test_empty_text_produces_no_chunks():
    assert chunk_text("", chunk_size=1000, overlap=100) == []
    assert chunk_text("   \n\n  ", chunk_size=1000, overlap=100) == []


def test_control_heading_forces_a_new_chunk_even_without_blank_line():
    # PDF text extraction often runs one control straight into the next with no
    # blank line between them -- the heading itself must still force a split.
    text = (
        "Some closing discussion for the previous control with no blank line "
        "separating it from what follows.\n"
        "SI-5 SECURITY ALERTS, ADVISORIES, AND DIRECTIVES\n"
        "Control: a. Receive system security alerts, advisories, and directives."
    )
    chunks = chunk_text(text, chunk_size=1000, overlap=100)

    assert len(chunks) == 2
    assert chunks[1].text.startswith("SI-5 SECURITY ALERTS")


def test_enhancement_heading_forces_a_new_chunk():
    text = (
        "Discussion of the base control continues here with no blank line.\n"
        "(1) AUTOMATED ALERTS AND ADVISORIES\n"
        "Broadcast security alert and advisory information throughout the organization."
    )
    chunks = chunk_text(text, chunk_size=1000, overlap=100)

    assert len(chunks) == 2
    assert chunks[1].text.startswith("(1) AUTOMATED ALERTS AND ADVISORIES")


def test_heading_boundary_prevents_cross_control_truncation():
    # Regression test for the real bug: a chunk_size cutoff must never land
    # mid-control, merging one control's tail with the next control's start.
    filler = "word " * 200
    text = (
        f"(24) SYSTEM MONITORING | INDICATORS OF COMPROMISE\n{filler}\n"
        f"SI-5 SECURITY ALERTS, ADVISORIES, AND DIRECTIVES\n{filler}"
    )
    chunks = chunk_text(text, chunk_size=1200, overlap=100)

    for chunk in chunks:
        assert not (
            "INDICATORS OF COMPROMISE" in chunk.text and "SI-5 SECURITY ALERTS" in chunk.text
        )
    assert any(chunk.text.startswith("SI-5 SECURITY ALERTS") for chunk in chunks)
