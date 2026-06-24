FROM python:3.12-slim

# ---------- system deps ----------
RUN apt-get update && apt-get install -y --no-install-recommends \
        procps \
    && rm -rf /var/lib/apt/lists/*

# ---------- python deps ----------
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------- application ----------
COPY app/ .

# Persistent storage for uploads (transient) and outputs (served for download)
VOLUME ["/data/uploads", "/data/outputs"]

EXPOSE 8000

# Single worker is intentional: pandas + large CSV processing is CPU-bound.
# Scale by running multiple containers behind a load balancer instead.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
