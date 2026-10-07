#!/usr/bin/env bash
# Run tests/test_nano_integration.py and the sqlite3 conformance suite
# (tests/test_sqlite3_conformance.py) against a throwaway HeliosDB Nano server.
#
#   scripts/nano-integration-test.sh [extra pytest arguments]
#
# Starts the official image on a private Docker network created for this run
# (no ports published on the host), with a random password kept in a 0600 file
# that only the server reads, runs the integration tests, and removes the
# container, the network and the password file on exit.
#
# Environment:
#   NANO_IMAGE  image to test (default ghcr.io/heliosdatabase/heliosdb-nano:4.41.0)
#   PYTHON      interpreter with pytest and psycopg2 installed (default python3)
#
# The tests reach the server at its address on the private network, which
# needs a Linux Docker host (Docker Desktop does not route to container IPs).
set -euo pipefail

IMAGE="${NANO_IMAGE:-ghcr.io/heliosdatabase/heliosdb-nano:4.41.0}"
PYTHON="${PYTHON:-python3}"
PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="sdk-nano-test-$("$PYTHON" -c 'import secrets; print(secrets.token_hex(4))')"
WORKDIR="$(mktemp -d)"
PASSWORD_FILE="$WORKDIR/password"

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker network rm "$NAME" >/dev/null 2>&1 || true
  rm -rf "$WORKDIR"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

chmod 700 "$WORKDIR"
(umask 077 && "$PYTHON" -c 'import secrets; print(secrets.token_hex(16))' >"$PASSWORD_FILE")
chmod 600 "$PASSWORD_FILE"

docker network create "$NAME" >/dev/null
# The server runs as the calling user, so the 0600 password file needs no
# wider permissions; the image's entrypoint reads it via
# HELIOSDB_PASSWORD_FILE and enables scram-sha-256. --memory: no data volume.
docker run -d --name "$NAME" --network "$NAME" \
  --user "$(id -u):$(id -g)" \
  -v "$PASSWORD_FILE:/run/secrets/heliosdb_password:ro" \
  -e HELIOSDB_PASSWORD_FILE=/run/secrets/heliosdb_password \
  "$IMAGE" start --memory >/dev/null

HOST="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$NAME")"
ready=0
for _ in $(seq 1 120); do
  if (exec 3<>"/dev/tcp/$HOST/5432") 2>/dev/null; then
    ready=1
    break
  fi
  sleep 0.5
done
if [ "$ready" -ne 1 ]; then
  echo "HeliosDB Nano did not start listening on $HOST:5432; last log lines:" >&2
  docker logs --tail 20 "$NAME" 2>&1 | grep -vF -f "$PASSWORD_FILE" >&2 || true
  exit 1
fi

echo "Testing $IMAGE on a private network"
cd "$PACKAGE_DIR"
HELIOSDB_TEST_URL="postgresql://postgres@$HOST:5432/heliosdb" \
HELIOSDB_TEST_PASSWORD_FILE="$PASSWORD_FILE" \
  "$PYTHON" -m pytest tests/test_nano_integration.py tests/test_sqlite3_conformance.py "$@"
