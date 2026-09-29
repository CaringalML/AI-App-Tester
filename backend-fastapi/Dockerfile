# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Dependencies first so code-only changes reuse this layer.
COPY requirements.txt .
RUN pip install -r requirements.txt \
    # Headless shell only: the scanner never needs headed Chrome, and this is far smaller.
    && playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/*

# axe-core is baked in at build time, so scans never depend on a CDN at runtime.
ARG AXE_VERSION=4.10.2
COPY scripts/fetch_axe.py scripts/fetch_axe.py
RUN python scripts/fetch_axe.py ${AXE_VERSION}

COPY app app

# Run unprivileged. Playwright launches Chromium without its sandbox by default,
# which is what container runtimes like Fargate need.
RUN useradd --create-home --uid 10001 scanner \
    && mkdir -p /app/.artifacts \
    && chown -R scanner /app/.artifacts
USER scanner

EXPOSE 8000

# One worker on purpose: scans run as background tasks inside this process,
# and the concurrency cap and rate limiter live in its memory.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "20"]
