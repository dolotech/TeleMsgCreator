FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir ".[web,media]"

RUN useradd --create-home --uid 10001 telemsg \
 && mkdir -p /app/data /app/data/uploads \
 && chown -R telemsg:telemsg /app

USER telemsg

ENV TELEMSG_DB_PATH=/app/data/telemsg.sqlite3 \
    TELEMSG_UPLOAD_DIR=/app/data/uploads \
    TELEMSG_UI_HOST=0.0.0.0 \
    TELEMSG_UI_PORT=8765

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3).status==200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "telemsg", "serve"]
