# LLTPM — AI Technical Program Manager
# Multi-stage Docker build for production deployment

# ── Build stage ───────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN pip install --no-cache-dir hatchling

# Copy source
COPY pyproject.toml ./
COPY src/ src/

# Build wheel
RUN pip wheel . --no-deps -w /wheels

# ── Runtime stage ─────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL maintainer="LLTPM Team"
LABEL description="AI-powered Technical Program Manager agent"

# Security: non-root user
RUN groupadd -r lltpm && useradd -r -g lltpm -m lltpm

WORKDIR /app

# Install system deps for optional features
RUN apt-get update && apt-get install -y --no-install-recommends \
    espeak \
    && rm -rf /var/lib/apt/lists/*

# Install wheel from build stage
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl

# Install optional dependencies
RUN pip install --no-cache-dir \
    fastapi>=0.115.0 \
    uvicorn>=0.30.0 \
    slack-sdk>=3.0 \
    && rm -rf /root/.cache

# Data volume for persistence
VOLUME /data
ENV LLTPM_DATA_DIR=/data

# Switch to non-root
USER lltpm

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "from lltpm.config import LLTPMConfig; print('ok')" || exit 1

# Default: start dashboard on port 8080
EXPOSE 8080
ENV PORT=8080

CMD ["python", "-m", "uvicorn", "lltpm.dashboard:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
