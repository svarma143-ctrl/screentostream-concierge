FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Install the package and its runtime dependencies first for better layer reuse.
COPY pyproject.toml README.md ./
COPY app ./app
COPY data ./data
COPY docs ./docs
# hadolint ignore=DL3013
RUN pip install --upgrade pip && pip install .

RUN mkdir -p /app/runtime/frames /app/runtime/media

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
