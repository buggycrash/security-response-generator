"""Parse and validate the CSV input for bulk control-response generation."""

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from security_response_generator import config

_REQUIRED_COLUMNS = ("control id", "user added context")


@dataclass(frozen=True)
class BulkRow:
    control_id: str
    context: str
    row_number: int


class CsvValidationError(Exception):
    """Raised with every problem found in the file, not just the first."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _normalize_header(name: str) -> str:
    return name.strip().casefold()


def parse_bulk_csv(path: Path) -> list[BulkRow]:
    """Read and validate a bulk-generation CSV.

    Raises CsvValidationError (with every problem found, not just the first)
    if the file is malformed. Does not check whether a control ID actually
    exists in the ingested NIST baseline -- that requires a live Chroma query
    and is deferred to generation time.
    """
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise CsvValidationError(["CSV file is empty (no header row)."]) from None

        normalized_header = [_normalize_header(name) for name in header]
        missing = [name for name in _REQUIRED_COLUMNS if name not in normalized_header]
        if missing:
            raise CsvValidationError([f"Missing required column(s): {', '.join(missing)}."])

        control_id_index = normalized_header.index("control id")
        context_index = normalized_header.index("user added context")

        errors: list[str] = []
        rows: list[BulkRow] = []
        seen_control_ids: dict[str, int] = {}

        for offset, raw_row in enumerate(reader, start=2):
            if not any(field.strip() for field in raw_row):
                continue

            control_id = (
                raw_row[control_id_index].strip() if control_id_index < len(raw_row) else ""
            )
            context = raw_row[context_index].strip() if context_index < len(raw_row) else ""

            if not control_id:
                errors.append(f"Row {offset}: Control ID is empty.")
                continue

            if not re.fullmatch(config.CONTROL_ID_PATTERN, control_id):
                errors.append(f"Row {offset}: '{control_id}' is not a valid control ID.")
                continue

            if control_id in seen_control_ids:
                errors.append(
                    f"Row {offset}: duplicate control ID '{control_id}' "
                    f"(first seen at row {seen_control_ids[control_id]})."
                )
                continue

            seen_control_ids[control_id] = offset
            rows.append(BulkRow(control_id=control_id, context=context, row_number=offset))

    if not rows and not errors:
        errors.append("CSV file has no data rows.")

    if len(rows) > config.MAX_BULK_CONTROLS:
        errors.append(
            f"CSV has {len(rows)} control(s), which exceeds the limit of "
            f"{config.MAX_BULK_CONTROLS} (override with SRG_MAX_BULK_CONTROLS)."
        )

    if errors:
        raise CsvValidationError(errors)

    return rows
