#!/usr/bin/env bash
# run.sh
# ------
# Builds and starts the ADS-B Cleaner container using Apple Container.
# Replaces docker-compose.yml for use with the native macOS container CLI.
#
# Prerequisites:
#   - Apple Container installed and the system daemon running:
#       container system start
#   - Run this script from the project root (same directory as Dockerfile)
#
# Usage:
#   ./run.sh            # build image and start container
#   ./run.sh --stop     # stop and remove the container
#   ./run.sh --logs     # tail live logs
#   ./run.sh --rebuild  # force a fresh image build, then start

set -euo pipefail

IMAGE="adsb-cleaner:latest"
CONTAINER_NAME="adsb-cleaner"
PORT="8000"
VOL_UPLOADS="adsb_uploads"
VOL_OUTPUTS="adsb_outputs"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log()  { echo "[run.sh] $*"; }
ok()   { echo "[run.sh] ✓ $*"; }
fail() { echo "[run.sh] ✗ $*" >&2; exit 1; }

require_container_cli() {
    command -v container &>/dev/null \
        || fail "'container' CLI not found. Install it from https://github.com/apple/container/releases"
}

ensure_volume() {
    local vol="$1"
    if container volume list | grep -q "^${vol}"; then
        ok "Volume '${vol}' already exists."
    else
        container volume create "${vol}"
        ok "Created volume '${vol}'."
    fi
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_stop() {
    log "Stopping container '${CONTAINER_NAME}'..."
    container stop "${CONTAINER_NAME}" 2>/dev/null && ok "Stopped." || log "Container was not running."
    container rm   "${CONTAINER_NAME}" 2>/dev/null && ok "Removed." || log "Container already removed."
}

cmd_logs() {
    log "Tailing logs for '${CONTAINER_NAME}' (Ctrl-C to exit)..."
    container logs --follow "${CONTAINER_NAME}"
}

cmd_build() {
    log "Building image '${IMAGE}'..."
    container build --tag "${IMAGE}" .
    ok "Image built."
}

cmd_start() {
    # Create named volumes if they don't already exist
    ensure_volume "${VOL_UPLOADS}"
    ensure_volume "${VOL_OUTPUTS}"

    # Stop any previous instance gracefully
    container stop "${CONTAINER_NAME}" 2>/dev/null || true
    container rm   "${CONTAINER_NAME}" 2>/dev/null || true

    log "Starting container '${CONTAINER_NAME}' on port ${PORT}..."

    container run \
        --detach \
        --name    "${CONTAINER_NAME}" \
        --publish "${PORT}:8000" \
        --volume  "${VOL_UPLOADS}:/data/uploads" \
        --volume  "${VOL_OUTPUTS}:/data/outputs" \
        --env     "MAX_UPLOAD_BYTES=10737418240" \
        "${IMAGE}"

    ok "Container started."
    echo ""
    echo "  Service : http://localhost:${PORT}"
    echo "  Health  : http://localhost:${PORT}/health"
    echo "  Logs    : ./run.sh --logs"
    echo "  Stop    : ./run.sh --stop"
    echo ""
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

require_container_cli

case "${1:-}" in
    --stop)
        cmd_stop
        ;;
    --logs)
        cmd_logs
        ;;
    --rebuild)
        cmd_stop
        cmd_build
        cmd_start
        ;;
    "")
        # Default: build (skip if image already exists) then start
        if ! container image list | grep -q "${IMAGE}"; then
            cmd_build
        else
            ok "Image '${IMAGE}' already exists. Use --rebuild to force a fresh build."
        fi
        cmd_start
        ;;
    *)
        echo "Usage: $0 [--stop | --logs | --rebuild]"
        exit 1
        ;;
esac
