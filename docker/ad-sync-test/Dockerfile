FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ldap-utils \
    && rm -rf /var/lib/apt/lists/*

COPY sync_from_ldap.py /usr/local/bin/gatewatch-ad-sync

RUN chmod +x /usr/local/bin/gatewatch-ad-sync

ENTRYPOINT ["gatewatch-ad-sync"]
