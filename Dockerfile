# syntax=docker/dockerfile:1.7
#
# Yantresh OS — FastAPI seam image. ARM64-native (Oracle Ampere A1) and
# AMD64-compatible. Multi-stage: build deps into a venv as root, then run
# as an unprivileged, read-only-rootfs user with nothing but that venv,
# the source tree, and two writable bind-mounted state dirs.

FROM python:3.12-slim AS builder

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY requirements-api.txt requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements-api.txt


FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="yantresh-os" \
      org.opencontainers.image.description="Amatya/Karta orchestrator — FastAPI seam"

# Fixed UID/GID so the host can chown bind mounts predictably.
RUN groupadd --gid 1000 yantresh \
 && useradd --uid 1000 --gid yantresh --no-create-home --shell /usr/sbin/nologin yantresh

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Application code only — tests/, .git, docs excluded via .dockerignore.
COPY --chown=yantresh:yantresh . .

# Pre-create state dirs so they exist even before volumes are mounted,
# and are writable by the non-root user once they are.
RUN mkdir -p /app/.yantresh /app/workspace \
 && chown -R yantresh:yantresh /app/.yantresh /app/workspace

USER yantresh

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    YANTRESH_CONFIG=/app/yantresh.toml

EXPOSE 8000

# Pure-stdlib healthcheck — no curl/wget in the image (smaller surface).
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]

# --no-new-privileges, read_only rootfs, cap_drop ALL are set in compose;
# this image makes no assumption it can write outside /app/.yantresh,
# /app/workspace, and /tmp (mounted tmpfs).
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
