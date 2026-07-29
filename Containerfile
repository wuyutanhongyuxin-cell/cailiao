# cailiao - optional, offline-first container skeleton (Stage 6 v1).
#
# This is a DEPLOYMENT SKELETON only. It is NOT built or pushed by any test or
# quality gate, requires no registry/network at build time beyond the base image,
# and installs no third-party dependencies (the project is stdlib-only).
#
# Build (manual, optional):   podman build -t cailiao:local -f Containerfile .
# Run   (manual, optional):   podman run --rm -p 8000:8000 cailiao:local
#
# The server defaults to offline model mode (no network, no model call) unless a
# provider is explicitly configured via MATERIAL_LLM_* environment variables.

FROM python:3.12-slim

WORKDIR /app

# Copy first-party sources only. No `pip install` step: zero runtime deps.
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY rules/ ./rules/
COPY tools/ ./tools/

# Local HTTP server port.
EXPOSE 8000

# Offline-first: no MATERIAL_LLM_* set means offline model mode by default.
CMD ["python", "backend/server.py"]
