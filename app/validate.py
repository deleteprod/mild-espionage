"""
validate.py
-----------
Validates that an uploaded file is a well-formed ADS-B CSV with sensible
values in the expected columns.

Raises ValidationError (a plain ValueError subclass) with a human-readable
message if anything looks wrong.
"""

import csv
import io
import re

# Minimum rows to inspect during content validation.
# We sample this many non-empty rows rather than scanning the whole file,
# keeping validation fast even for large uploads.
SAMPLE_ROWS = 2_000

# Fraction of non-empty values that must pass each check.
# e.g. 0.95 means 95 % of populated cells must be valid.
PASS_THRESHOLD = 0.95

_HEX6_RE  = re.compile(r'^[0-9A-Fa-f]{6}$')
_SQUAWK_RE = re.compile(r'^\d{4}$')


class ValidationError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Individual field validators – return True/False
# ---------------------------------------------------------------------------

def _valid_icao(val: str) -> bool:
    return bool(_HEX6_RE.match(val))


def _valid_squawk(val: str) -> bool:
    return bool(_SQUAWK_RE.match(val))


def _valid_heading(val: str) -> bool:
    try:
        h = float(val)
        return 0.0 <= h <= 359.9
    except ValueError:
        return False


def _valid_lat(val: str) -> bool:
    try:
        return '.' in val and -90.0 <= float(val) <= 90.0
    except ValueError:
        return False


def _valid_lon(val: str) -> bool:
    try:
        return '.' in val and -180.0 <= float(val) <= 180.0
    except ValueError:
        return False


def _valid_altitude(val: str) -> bool:
    try:
        float(val)
        return True
    except ValueError:
        return False


def _valid_speed(val: str) -> bool:
    try:
        return float(val) >= 0
    except ValueError:
        return False


# Map: column index (0-based) -> (friendly name, validator)
COL_VALIDATORS = {
    4:  ("icao_hex",       _valid_icao),
    11: ("altitude",       _valid_altitude),
    12: ("speed",          _valid_speed),
    13: ("heading",        _valid_heading),
    14: ("latitude",       _valid_lat),
    15: ("longitude",      _valid_lon),
    17: ("squawk",         _valid_squawk),
}

# Minimum number of populated cells we need to see before we bother
# checking the pass-rate (avoids false failures on very sparse columns).
MIN_POPULATED = 10


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def validate(raw_bytes: bytes) -> None:
    """
    Validate raw file bytes.  Raises ValidationError with a descriptive
    message on the first problem found.
    """
    _check_text(raw_bytes)

    text = raw_bytes.decode("utf-8", errors="replace")
    _check_csv_structure(text)
    _check_column_contents(text)


# ---------------------------------------------------------------------------
# Step 1 – must be plain UTF-8 / ASCII text
# ---------------------------------------------------------------------------

def _check_text(raw: bytes) -> None:
    # Reject obvious binary signatures
    if raw[:4] in (b'%PDF', b'PK\x03\x04'):
        raise ValidationError("File appears to be binary (PDF or ZIP/Office format), not a CSV.")

    if b'\x00' in raw[:4096]:
        raise ValidationError("File contains null bytes and does not appear to be plain text.")

    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValidationError("File is not valid UTF-8 text.")


# ---------------------------------------------------------------------------
# Step 2 – must parse as CSV with enough columns
# ---------------------------------------------------------------------------

def _check_csv_structure(text: str) -> None:
    reader = csv.reader(io.StringIO(text))
    short_rows = 0
    checked = 0

    for row in reader:
        if not any(row):          # skip blank lines
            continue
        if len(row) < 18:
            short_rows += 1
        checked += 1
        if checked >= SAMPLE_ROWS:
            break

    if checked == 0:
        raise ValidationError("File appears to be empty.")

    if short_rows / checked > 0.5:
        raise ValidationError(
            f"{short_rows}/{checked} sampled rows have fewer than 18 columns. "
            "This does not look like an ADS-B SBS CSV file."
        )


# ---------------------------------------------------------------------------
# Step 3 – spot-check column contents
# ---------------------------------------------------------------------------

def _check_column_contents(text: str) -> None:
    # Tallies: col_idx -> [pass_count, total_non_empty]
    tallies: dict[int, list[int]] = {i: [0, 0] for i in COL_VALIDATORS}

    reader = csv.reader(io.StringIO(text))
    checked = 0
    for row in reader:
        if not any(row):
            continue
        for col_idx, (_, validator) in COL_VALIDATORS.items():
            if col_idx >= len(row):
                continue
            val = row[col_idx].strip()
            if not val:
                continue
            tallies[col_idx][1] += 1
            if validator(val):
                tallies[col_idx][0] += 1
        checked += 1
        if checked >= SAMPLE_ROWS:
            break

    failures = []
    for col_idx, (name, _) in COL_VALIDATORS.items():
        passed, total = tallies[col_idx]
        if total < MIN_POPULATED:
            continue   # not enough data to judge
        rate = passed / total
        if rate < PASS_THRESHOLD:
            pct = round((1 - rate) * 100, 1)
            failures.append(
                f"  • column '{name}' (position {col_idx + 1}): "
                f"{pct}% of values failed validation "
                f"({total - passed}/{total} invalid)"
            )

    if failures:
        raise ValidationError(
            "Column content validation failed:\n" + "\n".join(failures)
        )
