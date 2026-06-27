#!/bin/bash
set -e
# One-time: create a stable self-signed code-signing identity in a DEDICATED keychain
# (known password) so the TCC (Screen Recording) grant survives rebuilds and codesign
# works non-interactively over SSH. Run ON the mini. Reversible:
#   security delete-keychain lumen.keychain
NAME="${1:-Lumen Dev}"
KC="lumen.keychain"
KCPASS="lumen"

if security find-identity -v -p codesigning "$KC" 2>/dev/null | grep -q "$NAME"; then
  echo "identity '$NAME' already exists in $KC"; exit 0
fi

# Dedicated keychain with a known password (headless-friendly, login keychain untouched)
security create-keychain -p "$KCPASS" "$KC" 2>/dev/null || true
security set-keychain-settings "$KC"            # no auto-lock timeout
security unlock-keychain -p "$KCPASS" "$KC"
# Prepend to the user search list so codesign can find the identity
security list-keychains -d user -s "$KC" $(security list-keychains -d user | sed 's/"//g')

TMP=$(mktemp -d)
cat > "$TMP/ext.cnf" <<EOF
[req]
distinguished_name=dn
x509_extensions=v3
prompt=no
[dn]
CN=$NAME
[v3]
basicConstraints=critical,CA:false
keyUsage=critical,digitalSignature
extendedKeyUsage=critical,codeSigning
EOF
openssl req -x509 -newkey rsa:2048 -keyout "$TMP/key.pem" -out "$TMP/cert.pem" \
  -days 3650 -nodes -config "$TMP/ext.cnf"
openssl pkcs12 -export -inkey "$TMP/key.pem" -in "$TMP/cert.pem" \
  -out "$TMP/id.p12" -passout pass:"$KCPASS" -name "$NAME"
security import "$TMP/id.p12" -k "$KC" -P "$KCPASS" -T /usr/bin/codesign
security set-key-partition-list -S apple-tool:,apple: -s -k "$KCPASS" "$KC" >/dev/null
rm -rf "$TMP"
echo "created '$NAME' in $KC"
security find-identity -v -p codesigning "$KC" | grep "$NAME"
