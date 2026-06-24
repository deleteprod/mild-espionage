"""
app.py  –  FastAPI service for ADS-B CSV cleaning
--------------------------------------------------
Endpoints
  POST /upload        multipart file upload → triggers validation + cleaning
  GET  /download/{id} download the cleaned CSV
  GET  /health        liveness check

Cleaned output files are automatically deleted 4 hours after creation by a
background asyncio task that wakes up every 15 minutes.
"""

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

import adsb_clean
from validate import ValidationError, validate

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

UPLOAD_DIR = Path("/data/uploads")
OUTPUT_DIR = Path("/data/outputs")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_BYTES       = 10 * 1024 ** 3   # 10 GB hard ceiling
OUTPUT_MAX_AGE_SECONDS = 4 * 60 * 60      # 4 hours
CLEANUP_INTERVAL_SECONDS = 15 * 60        # sweep every 15 minutes


# ---------------------------------------------------------------------------
# Background cleanup task
# ---------------------------------------------------------------------------

async def _cleanup_loop() -> None:
    """Periodically delete output CSVs older than OUTPUT_MAX_AGE_SECONDS."""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        now = time.time()
        deleted = 0
        for f in OUTPUT_DIR.glob("*_clean.csv"):
            try:
                age = now - f.stat().st_mtime
                if age > OUTPUT_MAX_AGE_SECONDS:
                    f.unlink()
                    deleted += 1
                    log.info("Cleanup: removed expired file %s (age %.0fs)", f.name, age)
            except FileNotFoundError:
                pass  # already gone – harmless race condition
            except Exception as exc:
                log.warning("Cleanup: could not remove %s: %s", f.name, exc)
        if deleted:
            log.info("Cleanup pass complete: %d file(s) removed.", deleted)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_cleanup_loop())
    log.info(
        "Cleanup task started – output files expire after %dh, checked every %dm.",
        OUTPUT_MAX_AGE_SECONDS // 3600,
        CLEANUP_INTERVAL_SECONDS // 60,
    )
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="ADS-B CSV Cleaner", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    """
    Accept a raw ADS-B CSV, validate it, clean it, and return:
      {
        "download_url":            "/download/<id>",
        "processing_time_seconds": <float>,
        "output_rows":             <int>,
        "expires_at_utc_epoch":    <int>,
        "expires_in_seconds":      <int>
      }
    """

    # -- 1. Read upload ---------------------------------------------------
    raw = await file.read()

    if len(raw) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {MAX_UPLOAD_BYTES // 1024**3} GB size limit.",
        )

    log.info("Received upload: filename=%s  size=%d bytes", file.filename, len(raw))

    # -- 2. Validate -------------------------------------------------------
    try:
        validate(raw)
    except ValidationError as exc:
        log.warning("Validation failed: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))

    # -- 3. Write to a temp input file ------------------------------------
    job_id      = uuid.uuid4().hex
    input_path  = UPLOAD_DIR / f"{job_id}_input.csv"
    output_path = OUTPUT_DIR / f"{job_id}_clean.csv"

    input_path.write_bytes(raw)
    log.info("Job %s: saved upload to %s", job_id, input_path)

    # -- 4. Clean ---------------------------------------------------------
    try:
        rows_written, elapsed = adsb_clean.clean(input_path, output_path)
    except Exception as exc:
        log.error("Job %s: processing error: %s", job_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Processing error: {exc}")
    finally:
        input_path.unlink(missing_ok=True)   # raw upload no longer needed

    log.info("Job %s: complete – %d rows written in %.3fs", job_id, rows_written, elapsed)

    expires_at       = int(time.time()) + OUTPUT_MAX_AGE_SECONDS
    expires_at_human = datetime.fromtimestamp(expires_at, tz=timezone.utc).strftime("%a %d %b %H:%M:%S")

    return JSONResponse({
        "download_url":            f"/download/{job_id}",
        "processing_time_seconds": elapsed,
        "output_rows":             rows_written,
        "expires_at_utc_epoch":    expires_at,
        "expires_at_utc":          expires_at_human,
        "expires_in_seconds":      OUTPUT_MAX_AGE_SECONDS,
    })


@app.get("/download/{job_id}")
def download(job_id: str):
    """Stream the cleaned CSV back to the caller."""
    # Sanitise job_id – must be a 32-char hex string (uuid4 without hyphens)
    if not job_id.isalnum() or len(job_id) != 32:
        raise HTTPException(status_code=400, detail="Invalid job ID.")

    output_path = OUTPUT_DIR / f"{job_id}_clean.csv"
    if not output_path.exists():
        raise HTTPException(
            status_code=404,
            detail="File not found. It may have expired or the ID is incorrect.",
        )

    age_seconds = time.time() - output_path.stat().st_mtime
    remaining   = max(0, int(OUTPUT_MAX_AGE_SECONDS - age_seconds))

    return FileResponse(
        path=output_path,
        media_type="text/csv",
        filename="adsb_clean.csv",
        headers={"X-Expires-In-Seconds": str(remaining)},
    )
