#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PACKAGE_NAME="$(basename -- "$PACKAGE_ROOT")"
: "${PERSONAL_DIET_PANTRY_DATA_DIR:?Set PERSONAL_DIET_PANTRY_DATA_DIR to an isolated data directory}"
DATA_DIR="$PERSONAL_DIET_PANTRY_DATA_DIR"
export PERSONAL_DIET_PANTRY_DATA_DIR="$DATA_DIR"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd -- "$PACKAGE_ROOT"
npm run build

VERSION="$(node -p "require('./package.json').version")"
ARCHIVE_DIR="$PACKAGE_ROOT/dist-package"
SOURCE_ARCHIVE_PATH="$ARCHIVE_DIR/$PACKAGE_NAME-$VERSION-source.tar.gz"
NPM_ARCHIVE_PATH="$ARCHIVE_DIR/$PACKAGE_NAME-$VERSION.tgz"
INSTALLABLE_ARCHIVE_PATH="$ARCHIVE_DIR/$PACKAGE_NAME-$VERSION-installable.tgz"

"$PYTHON_BIN" "$SCRIPT_DIR/reproducible_archive.py" \
  --package-root "$PACKAGE_ROOT" \
  --output "$SOURCE_ARCHIVE_PATH"

mkdir -p -- "$ARCHIVE_DIR"
rm -f -- "$NPM_ARCHIVE_PATH"
npm pack --pack-destination "$ARCHIVE_DIR" >/dev/null
mv -f -- "$NPM_ARCHIVE_PATH" "$INSTALLABLE_ARCHIVE_PATH"

printf 'Created installable archive: %s\n' "$INSTALLABLE_ARCHIVE_PATH"
