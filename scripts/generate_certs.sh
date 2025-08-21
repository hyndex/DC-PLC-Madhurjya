#!/usr/bin/env bash
set -euo pipefail

# Generate ISO 15118 test certificates using the upstream script
REPO_ROOT="$(git rev-parse --show-toplevel)"
PKI_SRC="$(python3 -c 'import pathlib, iso15118; print(pathlib.Path(iso15118.__file__).resolve().parent / "shared/pki")')"
PKI_LINK="$REPO_ROOT/pki"
CERT_FILE="$PKI_LINK/iso15118_2/certs/seccLeafCert.pem"

# Ensure predictable certificate directory
ln -sfn "$PKI_SRC" "$PKI_LINK"

# Skip generation if certificates already exist
if [ -f "$CERT_FILE" ]; then
    echo "ISO 15118 certificates already exist at $PKI_LINK; skipping generation."
    exit 0
fi

pushd "$PKI_SRC" >/dev/null
./create_certs.sh -v iso-2
popd >/dev/null

echo "ISO 15118 certificates generated at $PKI_LINK"
