from security_response_generator.ingest import loaders
from security_response_generator.ingest.loaders import (
    _is_control_crosswalk_table,
    iter_source_files,
    load_document,
)


def test_iter_source_files_excludes_readme_and_gitkeep(tmp_path):
    (tmp_path / "README.md").write_text("placeholder")
    (tmp_path / ".gitkeep").write_text("")
    (tmp_path / "control.md").write_text("real content")

    found = list(iter_source_files(tmp_path))

    assert [p.name for p in found] == ["control.md"]


def test_iter_source_files_filters_unsupported_extensions(tmp_path):
    (tmp_path / "notes.txt").write_text("text")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")

    found = list(iter_source_files(tmp_path))

    assert [p.name for p in found] == ["notes.txt"]


def test_iter_source_files_missing_directory_yields_nothing(tmp_path):
    assert list(iter_source_files(tmp_path / "does_not_exist")) == []


def test_load_document_reads_markdown_with_relative_path(tmp_path):
    (tmp_path / "sub").mkdir()
    file_path = tmp_path / "sub" / "control.md"
    file_path.write_text("SI-5 content")

    document = load_document(file_path, tmp_path)

    assert document.source_path == "sub/control.md"
    assert document.text == "SI-5 content"


def test_is_control_crosswalk_table_detects_appendix_c_page():
    page_text = (
        "APPENDIX C   PAGE 463\n"
        "CONTROL \nNUMBER \nCONTROL NAME \nCONTROL ENHANCEMENT NAME \n"
        "IMPLEMENTED \nBY \nASSURANCE \n"
        "SI-5 Security Alerts, Advisories, and Directives O v\n"
    )
    assert _is_control_crosswalk_table(page_text) is True


def test_is_control_crosswalk_table_ignores_narrative_control_text():
    page_text = (
        "SI-5 SECURITY ALERTS, ADVISORIES, AND DIRECTIVES\n"
        "Control: a. Receive system security alerts, advisories, and directives."
    )
    assert _is_control_crosswalk_table(page_text) is False


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakePdfReader:
    def __init__(self, path):
        self.pages = [
            _FakePage("SI-5 SECURITY ALERTS, ADVISORIES, AND DIRECTIVES\nControl: a. Receive..."),
            _FakePage(
                "APPENDIX C   PAGE 463\n"
                "CONTROL \nNUMBER \nCONTROL NAME \nCONTROL ENHANCEMENT NAME \n"
                "IMPLEMENTED \nBY \nASSURANCE \nSI-5 Security Alerts... O v\n"
            ),
        ]


def test_load_pdf_skips_control_crosswalk_table_pages(monkeypatch, tmp_path):
    monkeypatch.setattr(loaders, "PdfReader", _FakePdfReader)
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"")

    document = load_document(pdf_path, tmp_path)

    assert "SI-5 SECURITY ALERTS" in document.text
    assert "CONTROL ENHANCEMENT NAME" not in document.text
