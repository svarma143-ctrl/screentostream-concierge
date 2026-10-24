# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Match the uv version used to produce uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PORT=8000

WORKDIR /app

# Install the exact locked runtime set. The project is built non-editable so
# the image does not depend on the source tree being writable at runtime.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY app ./app
COPY data ./data
COPY docs ./docs
RUN uv sync --frozen --no-dev --no-editable

# Run as an unprivileged user. The named runtime volume inherits this owner.
RUN useradd --system --uid 10001 --user-group --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/runtime/frames /app/runtime/media \
    && chown -R 10001:10001 /app

ENV HOME=/home/appuser \
    PATH="/app/.venv/bin:$PATH"

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
