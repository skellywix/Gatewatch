FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        dnsutils \
        krb5-user \
        ldb-tools \
        ldap-utils \
        procps \
        samba \
        samba-ad-provision \
        samba-dsdb-modules \
        samba-vfs-modules \
        winbind \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /usr/local/bin/gatewatch-ad-entrypoint.sh
COPY export_gatewatch_ad.py /usr/local/bin/export-gatewatch-ad

RUN chmod +x /usr/local/bin/gatewatch-ad-entrypoint.sh /usr/local/bin/export-gatewatch-ad

VOLUME ["/var/lib/samba", "/var/cache/samba", "/var/log/samba"]

EXPOSE 53/tcp 53/udp 88/tcp 88/udp 135/tcp 139/tcp 389/tcp 389/udp 445/tcp 464/tcp 464/udp 636/tcp 3268/tcp 3269/tcp

ENTRYPOINT ["/usr/local/bin/gatewatch-ad-entrypoint.sh"]
