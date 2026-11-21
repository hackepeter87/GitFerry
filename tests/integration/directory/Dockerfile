# Standalone fixture image using the repository's existing digest-pinned Python
# base. The resulting image digest is recorded by the standalone run.
ARG BASE_IMAGE=python:3.13.9-slim@sha256:326df678c20c78d465db501563f3492d17c42a4afe33a1f2bf5406a1d56b0e86
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
      slapd=2.6.10+dfsg-1 \
      ldap-utils=2.6.10+dfsg-1 \
      openssl \
    && rm -rf /var/lib/apt/lists/*

COPY slapd.conf /etc/ldap/slapd.conf
COPY base.ldif /etc/ldap/base.ldif
COPY entrypoint.sh /usr/local/bin/directory-entrypoint
RUN chmod 0755 /usr/local/bin/directory-entrypoint \
    && mkdir -p /run/slapd /var/lib/ldap \
    && chown -R openldap:openldap /run/slapd /var/lib/ldap

EXPOSE 389 636
ENTRYPOINT ["/usr/local/bin/directory-entrypoint"]
