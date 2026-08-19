#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
IMAGE="${MCUTEST_IMAGE:-mcutest:0.2.0}"
BIN_DIR="${MCUTEST_BIN_DIR:-$HOME/.local/bin}"

docker build --pull -t "$IMAGE" "$SCRIPT_DIR"
mkdir -p "$BIN_DIR"
install -m 0755 "$SCRIPT_DIR/bin/mcutest" "$BIN_DIR/mcutest"

printf 'Installed mcutest to %s\n' "$BIN_DIR/mcutest"
printf 'Image: %s\n' "$IMAGE"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) printf 'Add %s to PATH.\n' "$BIN_DIR" ;;
esac
