FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ACCESS_REGISTER_HOST=0.0.0.0 \
    ACCESS_REGISTER_PORT=8087 \
    ACCESS_REGISTER_DB=/data/access_register.db \
    ACCESS_REGISTER_AUTH_MODE=trusted_proxy

WORKDIR /app

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin gatewatch \
    && mkdir -p /data \
    && chown -R gatewatch:gatewatch /data /app

COPY --chown=gatewatch:gatewatch app.py README.md ./
COPY --chown=gatewatch:gatewatch web ./web

USER gatewatch

VOLUME ["/data"]
EXPOSE 8087

CMD ["python", "app.py"]
