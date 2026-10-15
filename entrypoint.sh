#!/bin/sh
set -eu
PUID="${PUID:-99}"
PGID="${PGID:-100}"
APPDATA_DIR="${APPDATA_DIR:-/appdata}"
INPUT_DIR="${INPUT_DIR:-/input}"
OUTPUT_DIR="${OUTPUT_DIR:-/output}"
mkdir -p "$APPDATA_DIR" "$INPUT_DIR" "$OUTPUT_DIR"
if [ "$(id -u)" = "0" ]; then
  groupmod -o -g "$PGID" app
  usermod -o -u "$PUID" -g app app
  chown "$PUID:$PGID" "$APPDATA_DIR"
  exec gosu "$PUID:$PGID" "$@"
fi
exec "$@"
