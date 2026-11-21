#!/bin/sh
set -eu

umask 077
mkdir -p /run/directory-ca /run/slapd /var/lib/ldap
if [ ! -f /run/directory-ca/ca.pem ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
    -subj '/CN=synthetic-directory-ca' \
    -keyout /run/directory-ca/ca.key -out /run/directory-ca/ca.pem >/dev/null 2>&1
  openssl req -newkey rsa:2048 -nodes \
    -subj '/CN=openldap' \
    -addext 'subjectAltName=DNS:openldap,DNS:localhost,IP:127.0.0.1' \
    -keyout /run/directory-ca/server.key -out /run/directory-ca/server.csr >/dev/null 2>&1
  openssl x509 -req -days 1 -in /run/directory-ca/server.csr \
    -CA /run/directory-ca/ca.pem -CAkey /run/directory-ca/ca.key -CAcreateserial \
    -copy_extensions copy \
    -out /run/directory-ca/server.pem >/dev/null 2>&1
  chmod 0644 /run/directory-ca/ca.pem /run/directory-ca/server.pem
  chmod 0600 /run/directory-ca/ca.key /run/directory-ca/server.key
  chown openldap:openldap /run/directory-ca/server.key /run/directory-ca/server.pem
fi
directory_password='synthetic-directory-pass'
printf '%s' "$directory_password" > /run/slapd/admin-password
chmod 0600 /run/slapd/admin-password
directory_hash=$(slappasswd -n -h '{SSHA}' -T /run/slapd/admin-password)
sed "s#REPLACE_AT_START#$directory_hash#" /etc/ldap/slapd.conf > /run/slapd/slapd.conf
if [ ! -f /var/lib/ldap/.initialized ]; then
  sed "s#userPassword: synthetic-directory-pass#userPassword: $directory_hash#g" /etc/ldap/base.ldif > /run/slapd/base.ldif
  slapadd -f /run/slapd/slapd.conf -l /run/slapd/base.ldif
  touch /var/lib/ldap/.initialized
  chown -R openldap:openldap /var/lib/ldap /run/slapd
fi
exec slapd -f /run/slapd/slapd.conf -h 'ldap://0.0.0.0:389/ ldaps://0.0.0.0:636/' -u openldap -g openldap -d 1
