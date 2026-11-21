#!/bin/sh
set -eu
umask 077
mkdir -p /run/ad-ca /etc/samba /var/lib/samba /var/lib/samba/private

if [ ! -f /run/ad-ca/ca.pem ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
    -subj '/CN=synthetic-ad-ca' \
    -addext 'basicConstraints=critical,CA:TRUE' \
    -addext 'keyUsage=critical,keyCertSign,cRLSign' \
    -keyout /run/ad-ca/ca.key -out /run/ad-ca/ca.pem >/dev/null 2>&1
  openssl req -newkey rsa:2048 -nodes -subj '/CN=synthdc' \
    -addext 'subjectAltName=DNS:synthdc,DNS:localhost' \
    -keyout /run/ad-ca/server.key -out /run/ad-ca/server.csr >/dev/null 2>&1
  cat > /run/ad-ca/server.extensions <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:synthdc,DNS:localhost
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF
  openssl x509 -req -days 2 -in /run/ad-ca/server.csr \
    -CA /run/ad-ca/ca.pem -CAkey /run/ad-ca/ca.key -CAcreateserial \
    -extfile /run/ad-ca/server.extensions -out /run/ad-ca/server.pem >/dev/null 2>&1
  openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
    -subj '/CN=untrusted-synthetic-ad-ca' \
    -addext 'basicConstraints=critical,CA:TRUE' \
    -addext 'keyUsage=critical,keyCertSign,cRLSign' \
    -keyout /run/ad-ca/untrusted.key -out /run/ad-ca/untrusted.pem >/dev/null 2>&1
  chmod 0644 /run/ad-ca/ca.pem /run/ad-ca/server.pem
  chmod 0600 /run/ad-ca/ca.key /run/ad-ca/server.key
fi

if [ ! -f /var/lib/samba/private/sam.ldb ]; then
  AD_ADMIN_PASSWORD='Synthetic-Admin-Only-42!' /usr/bin/python3 /usr/local/bin/ad-provision
fi

exec /usr/sbin/samba -i -s /etc/samba/smb.conf --no-process-group
