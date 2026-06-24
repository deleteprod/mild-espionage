"""
adsb_clean.py  –  importable processing module
-----------------------------------------------
Called by the FastAPI app; returns (output_path, row_count, elapsed_seconds).
"""

import csv
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------------
# Hard-coded column layout (1-based in comments, 0-based in code)
# ---------------------------------------------------------------------------
#   col 5  = ICAO hex
#   col 7  = date
#   col 8  = time
#   col 11 = callsign
#   col 12 = altitude
#   col 13 = speed
#   col 14 = heading
#   col 15 = latitude
#   col 16 = longitude
#   col 17 = vertical_speed
#   col 18 = squawk

ICAO_IDX       = 4
DATE_IDX       = 6
TIME_IDX       = 7
CALLSIGN_IDX   = 10
ALTITUDE_IDX   = 11
SPEED_IDX      = 12
HEADING_IDX    = 13
LATITUDE_IDX   = 14
LONGITUDE_IDX  = 15
VERT_SPEED_IDX = 16
SQUAWK_IDX     = 17

FEATURE_COLS: dict[str, int] = {
    "callsign":       CALLSIGN_IDX,
    "altitude":       ALTITUDE_IDX,
    "speed":          SPEED_IDX,
    "heading":        HEADING_IDX,
    "latitude":       LATITUDE_IDX,
    "longitude":      LONGITUDE_IDX,
    "vertical_speed": VERT_SPEED_IDX,
    "squawk":         SQUAWK_IDX,
}

OUTPUT_FIELDS = ["icao_hex", "date", "time"] + list(FEATURE_COLS.keys())

CHUNK_SIZE = 200_000


def _empty_accumulator() -> dict:
    return {field: "" for field in FEATURE_COLS}


def _is_complete(acc: dict) -> bool:
    return all(acc[f] != "" for f in FEATURE_COLS)


def clean(input_path: str | Path, output_path: str | Path) -> tuple[int, float]:
    """
    Process input_path and write consolidated rows to output_path.
    Returns (rows_written, elapsed_seconds).
    """
    t0 = time.perf_counter()

    acc: dict[str, dict] = defaultdict(_empty_accumulator)
    last_date: dict[str, str] = defaultdict(str)
    last_time: dict[str, str] = defaultdict(str)
    total_written = 0

    with open(output_path, "w", newline="", encoding="utf-8") as out_fh:
        writer = csv.DictWriter(out_fh, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()

        for chunk in pd.read_csv(
            input_path,
            header=None,
            chunksize=CHUNK_SIZE,
            dtype=str,
            keep_default_na=False,
        ):
            for row in chunk.itertuples(index=False):
                cols = list(row)
                if len(cols) <= ICAO_IDX:
                    continue

                icao = cols[ICAO_IDX].strip()
                if not icao or icao.lower() == "nan":
                    continue

                date_val = cols[DATE_IDX].strip() if len(cols) > DATE_IDX else ""
                time_val = cols[TIME_IDX].strip() if len(cols) > TIME_IDX else ""
                if date_val and date_val.lower() != "nan":
                    last_date[icao] = date_val
                if time_val and time_val.lower() != "nan":
                    last_time[icao] = time_val

                a = acc[icao]
                for field, col_idx in FEATURE_COLS.items():
                    if a[field]:
                        continue
                    if len(cols) > col_idx:
                        val = cols[col_idx].strip()
                        if val and val.lower() != "nan":
                            a[field] = val

                if _is_complete(a):
                    writer.writerow({
                        "icao_hex": icao,
                        "date":     last_date[icao],
                        "time":     last_time[icao],
                        **a,
                    })
                    total_written += 1
                    acc[icao] = _empty_accumulator()

    return total_written, round(time.perf_counter() - t0, 3)
