#!/usr/bin/env bash
set -euo pipefail

# Generate ISO 15118 test certificates using the upstream script
REPO_ROOT="$(git rev-parse --show-toplevel)"
PKI_LINK="$REPO_ROOT/pki"
CERT_FILE="$PKI_LINK/iso15118_2/certs/seccLeafCert.pem"

# Prefer venv Python if present to resolve module paths
if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PY_BIN="$REPO_ROOT/.venv/bin/python"
else
  PY_BIN="${PYTHON:-python3}"
fi

# Try multiple strategies to locate the iso15118 shared/pki directory
guess_from_submodule="$REPO_ROOT/src/iso15118/iso15118/shared/pki"
guess_from_module="$($PY_BIN - <<'PY'
import pathlib, sys
try:
    import iso15118  # type: ignore
    base = pathlib.Path(iso15118.__file__).resolve().parent
    print(str(base / 'shared' / 'pki'))
except Exception:
    print('')
PY
)"

if [[ -d "$guess_from_submodule" ]]; then
  PKI_SRC="$guess_from_submodule"
elif [[ -n "$guess_from_module" && -d "$guess_from_module" ]]; then
  PKI_SRC="$guess_from_module"
else
  echo "Unable to locate iso15118/shared/pki. Ensure submodules are initialized or iso15118 is installed." >&2
  echo "Tried: $guess_from_submodule and $guess_from_module" >&2
  exit 1
fi

# Ensure predictable certificate directory
ln -sfn "$PKI_SRC" "$PKI_LINK"

# Skip generation if certificates already exist
if [[ -f "$CERT_FILE" ]]; then
  echo "ISO 15118 certificates already exist at $PKI_LINK; skipping generation."
  exit 0
fi

pushd "$PKI_SRC" >/dev/null
./create_certs.sh -v iso-2
popd >/dev/null

echo "ISO 15118 certificates generated at $PKI_LINK"
