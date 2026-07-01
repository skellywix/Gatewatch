FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GATEWATCH_HOST=0.0.0.0 \
    GATEWATCH_PORT=8087 \
    GATEWATCH_DB=/data/gatewatch.db \
    GATEWATCH_CONFIG_FILE=/data/gatewatch.env \
    GATEWATCH_ALLOW_INSECURE_NETWORK=1

WORKDIR /app

RUN addgroup -S gatewatch \
    && adduser -S -D -H -u 10001 -s /sbin/nologin -G gatewatch gatewatch \
    && mkdir -p /data \
    && chown -R gatewatch:gatewatch /data /app \
    && rm -rf /usr/local/lib/python*/site-packages/pip* /usr/local/bin/pip*

COPY --chown=gatewatch:gatewatch app.py README.md ./
COPY --chown=gatewatch:gatewatch web ./web

USER gatewatch

VOLUME ["/data"]
EXPOSE 8087

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import os, urllib.request; port=os.environ.get('GATEWATCH_PORT','8087'); urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=5).read()"

CMD ["python", "app.py"]
