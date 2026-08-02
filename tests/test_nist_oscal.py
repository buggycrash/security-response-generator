import json
from io import BytesIO

import pytest

from security_response_generator.ingest import nist_oscal
from security_response_generator.ingest.chunking import chunk_text


def _catalog_payload() -> bytes:
    return json.dumps(
        {
            "catalog": {
                "metadata": {
                    "title": (
                        "Electronic (OSCAL) Version of NIST SP 800-53 Rev 5.2.0 "
                        "Controls and SP 800-53A Rev 5.2.0 Assessment Procedures"
                    ),
                    "version": "5.2.0",
                    "last-modified": "2025-08-26T14:33:16Z",
                },
                "groups": [
                    {
                        "id": "si",
                        "title": "System and Information Integrity",
                        "controls": [
                            {
                                "id": "si-5",
                                "title": "Security Alerts, Advisories, and Directives",
                                "params": [
                                    {
                                        "id": "si-05_odp.01",
                                        "props": [
                                            {
                                                "name": "label",
                                                "value": "SI-05_ODP[01]",
                                            }
                                        ],
                                        "label": "external organizations",
                                    }
                                ],
                                "parts": [
                                    {
                                        "name": "statement",
                                        "parts": [
                                            {
                                                "name": "item",
                                                "props": [{"name": "label", "value": "a."}],
                                                "prose": (
                                                    "Receive alerts from "
                                                    "{{ insert: param, si-05_odp.01 }}."
                                                ),
                                            }
                                        ],
                                    },
                                    {
                                        "name": "guidance",
                                        "prose": "Use authoritative security advisories.",
                                    },
                                    {
                                        "name": "assessment-objective",
                                        "prose": "ASSESSMENT OBJECTIVE MUST NOT BE EXPORTED",
                                    },
                                    {
                                        "name": "assessment-method",
                                        "prose": "ASSESSMENT METHOD MUST NOT BE EXPORTED",
                                    },
                                ],
                                "links": [{"rel": "related", "href": "#ra-5"}],
                                "controls": [
                                    {
                                        "id": "si-5.1",
                                        "title": "Automated Alerts — and Advisories",
                                        "parts": [
                                            {
                                                "name": "statement",
                                                "prose": "Broadcast alerts automatically.",
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        }
    ).encode()


def test_convert_catalog_emits_chunker_compatible_control_sections():
    result = nist_oscal.convert_oscal_catalog(_catalog_payload(), "fixture.json")

    assert result.version == "5.2.0"
    assert result.control_count == 1
    assert result.enhancement_count == 1
    assert "SI-5 SECURITY ALERTS, ADVISORIES, AND DIRECTIVES" in result.markdown
    assert "(1) AUTOMATED ALERTS - AND ADVISORIES" in result.markdown
    assert "Control ID: SI-5(1)" in result.markdown

    chunks = chunk_text(result.markdown)
    assert any(chunk.text.startswith("SI-5 ") for chunk in chunks)
    assert any(chunk.text.startswith("(1) ") and "SI-5(1)" in chunk.text for chunk in chunks)


def test_convert_catalog_resolves_parameters_and_excludes_assessment_procedures():
    result = nist_oscal.convert_oscal_catalog(_catalog_payload(), "fixture.json")

    assert "[Organization-defined parameter: external organizations]" in result.markdown
    assert "SI-05_ODP[01]: external organizations" in result.markdown
    assert "Related Controls: RA-5" in result.markdown
    assert "ASSESSMENT OBJECTIVE" not in result.markdown
    assert "ASSESSMENT METHOD" not in result.markdown


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"not json", "not valid UTF-8 JSON"),
        (b"{}", "does not contain a catalog object"),
        (
            json.dumps(
                {
                    "catalog": {
                        "metadata": {"title": "Different catalog", "version": "1"},
                        "groups": [],
                    }
                }
            ).encode(),
            "not identified as NIST SP 800-53",
        ),
    ],
)
def test_convert_catalog_rejects_invalid_sources(payload, message):
    with pytest.raises(nist_oscal.CatalogError, match=message):
        nist_oscal.convert_oscal_catalog(payload, "fixture.json")


def test_load_oscal_source_reads_local_file(tmp_path):
    source = tmp_path / "catalog.json"
    source.write_bytes(_catalog_payload())

    assert nist_oscal.load_oscal_source(str(source)) == _catalog_payload()


def test_load_oscal_source_rejects_non_https_url():
    with pytest.raises(nist_oscal.CatalogError, match="must use HTTPS"):
        nist_oscal.load_oscal_source("http://example.test/catalog.json")


def test_load_oscal_source_downloads_https(monkeypatch):
    class FakeResponse:
        headers = {"Content-Length": str(len(_catalog_payload()))}

        def __enter__(self):
            self._stream = BytesIO(_catalog_payload())
            return self

        def __exit__(self, *args):
            return None

        def geturl(self):
            return "https://example.test/catalog.json"

        def read(self, size):
            return self._stream.read(size)

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(nist_oscal, "urlopen", fake_urlopen)

    result = nist_oscal.load_oscal_source("https://example.test/catalog.json")

    assert result == _catalog_payload()
    assert captured == {"url": "https://example.test/catalog.json", "timeout": 60}


def test_update_catalog_writes_generated_markdown(tmp_path):
    source = tmp_path / "catalog.json"
    output = tmp_path / "nested" / "catalog.md"
    source.write_bytes(_catalog_payload())

    result = nist_oscal.update_catalog(str(source), output)

    assert output.read_text() == result.markdown
    assert not list(output.parent.glob("*.tmp"))


def test_write_catalog_markdown_reports_output_error(tmp_path):
    output = tmp_path / "not-a-directory" / "catalog.md"
    output.parent.write_text("file")

    with pytest.raises(nist_oscal.CatalogError, match="Unable to write"):
        nist_oscal.write_catalog_markdown(output, "converted")
