# Isolated Samba AD DC lab. Keep the Python base aligned with the integration fixtures.
ARG BASE_IMAGE=python:3.13.9-slim@sha256:326df678c20c78d465db501563f3492d17c42a4afe33a1f2bf5406a1d56b0e86
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
ARG SAMBA_VERSION=2:4.22.11+dfsg-0+deb13u1
RUN apt-get update \
 && apt-cache policy samba \
 && apt-get install --no-install-recommends -y \
      samba=${SAMBA_VERSION} \
      samba-ad-dc=${SAMBA_VERSION} \
      samba-ad-provision=${SAMBA_VERSION} \
      samba-dsdb-modules=${SAMBA_VERSION} \
      python3-samba=${SAMBA_VERSION} \
      ldap-utils \
      openssl \
      krb5-user \
 && python -m pip install --no-cache-dir ldap3==2.9.1 \
 && rm -rf /var/lib/apt/lists/*

COPY smb.conf /etc/samba/smb.conf
COPY provision.py /usr/local/bin/ad-provision
COPY entrypoint.sh /usr/local/bin/ad-entrypoint
COPY worker.py /usr/local/bin/ad-worker
RUN chmod 0755 /usr/local/bin/ad-provision /usr/local/bin/ad-entrypoint /usr/local/bin/ad-worker
EXPOSE 389 636 88 53
ENTRYPOINT ["/usr/local/bin/ad-entrypoint"]
