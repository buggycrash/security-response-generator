from security_response_generator import config
from security_response_generator.generation import bulk_csv


def _write_csv(tmp_path, text, name="controls.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_bulk_csv_returns_rows_in_file_order(tmp_path):
    path = _write_csv(
        tmp_path,
        "Control ID,User added context\nAC-2,Uses Okta for account management.\nSI-5,\n",
    )

    rows = bulk_csv.parse_bulk_csv(path)

    assert [row.control_id for row in rows] == ["AC-2", "SI-5"]
    assert rows[0].context == "Uses Okta for account management."
    assert rows[0].row_number == 2
    assert rows[1].context == ""
    assert rows[1].row_number == 3


def test_parse_bulk_csv_tolerates_header_case_and_whitespace(tmp_path):
    path = _write_csv(tmp_path, " Control ID , USER ADDED CONTEXT \nAC-2,notes\n")

    rows = bulk_csv.parse_bulk_csv(path)

    assert rows == [bulk_csv.BulkRow(control_id="AC-2", context="notes", row_number=2)]


def test_parse_bulk_csv_rejects_missing_headers(tmp_path):
    path = _write_csv(tmp_path, "Control,Notes\nAC-2,notes\n")

    try:
        bulk_csv.parse_bulk_csv(path)
        raise AssertionError("expected CsvValidationError")
    except bulk_csv.CsvValidationError as exc:
        assert any("Missing required column" in error for error in exc.errors)


def test_parse_bulk_csv_rejects_empty_file(tmp_path):
    path = _write_csv(tmp_path, "")

    try:
        bulk_csv.parse_bulk_csv(path)
        raise AssertionError("expected CsvValidationError")
    except bulk_csv.CsvValidationError as exc:
        assert "empty" in exc.errors[0].lower()


def test_parse_bulk_csv_rejects_headers_only_file(tmp_path):
    path = _write_csv(tmp_path, "Control ID,User added context\n")

    try:
        bulk_csv.parse_bulk_csv(path)
        raise AssertionError("expected CsvValidationError")
    except bulk_csv.CsvValidationError as exc:
        assert "no data rows" in exc.errors[0].lower()


def test_parse_bulk_csv_rejects_too_many_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MAX_BULK_CONTROLS", 2)
    path = _write_csv(
        tmp_path,
        "Control ID,User added context\nAC-1,\nAC-2,\nAC-3,\n",
    )

    try:
        bulk_csv.parse_bulk_csv(path)
        raise AssertionError("expected CsvValidationError")
    except bulk_csv.CsvValidationError as exc:
        assert any("exceeds the limit of 2" in error for error in exc.errors)


def test_parse_bulk_csv_rejects_duplicate_control_id(tmp_path):
    path = _write_csv(
        tmp_path,
        "Control ID,User added context\nAC-2,first\nAC-2,second\n",
    )

    try:
        bulk_csv.parse_bulk_csv(path)
        raise AssertionError("expected CsvValidationError")
    except bulk_csv.CsvValidationError as exc:
        assert any("duplicate control ID 'AC-2'" in error for error in exc.errors)


def test_parse_bulk_csv_rejects_malformed_control_ids(tmp_path):
    path = _write_csv(
        tmp_path,
        "Control ID,User added context\nAC2,\nac-2,\nAC-2(x),\n",
    )

    try:
        bulk_csv.parse_bulk_csv(path)
        raise AssertionError("expected CsvValidationError")
    except bulk_csv.CsvValidationError as exc:
        assert len(exc.errors) == 3
        assert all("not a valid control ID" in error for error in exc.errors)


def test_parse_bulk_csv_rejects_empty_control_id_cell(tmp_path):
    path = _write_csv(tmp_path, "Control ID,User added context\n,some notes\n")

    try:
        bulk_csv.parse_bulk_csv(path)
        raise AssertionError("expected CsvValidationError")
    except bulk_csv.CsvValidationError as exc:
        assert "Row 2: Control ID is empty." in exc.errors


def test_parse_bulk_csv_ignores_extra_columns(tmp_path):
    path = _write_csv(
        tmp_path,
        "Extra,Control ID,User added context,Another\nfoo,AC-2,notes,bar\n",
    )

    rows = bulk_csv.parse_bulk_csv(path)

    assert rows == [bulk_csv.BulkRow(control_id="AC-2", context="notes", row_number=2)]


def test_parse_bulk_csv_reports_multiple_errors_together(tmp_path):
    path = _write_csv(
        tmp_path,
        "Control ID,User added context\nAC2,\n,notes\n",
    )

    try:
        bulk_csv.parse_bulk_csv(path)
        raise AssertionError("expected CsvValidationError")
    except bulk_csv.CsvValidationError as exc:
        assert len(exc.errors) == 2


def test_parse_bulk_csv_skips_trailing_blank_line(tmp_path):
    path = _write_csv(tmp_path, "Control ID,User added context\nAC-2,notes\n,\n")

    rows = bulk_csv.parse_bulk_csv(path)

    assert [row.control_id for row in rows] == ["AC-2"]
