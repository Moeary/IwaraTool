#!/usr/bin/env bash
# Release file names from v1.3.0 (docs/verification/flet/P9): the Flet builds
# become IwaraTool-*, the PySide6 builds IwaraTool-Classic-*.  Used by build.yml.
#   release_names.sh <dist-dir>
set -euo pipefail
cd "$1"
shopt -s nullglob
for f in IwaraTool-*; do
  case "$f" in
    IwaraTool-Flet-*|IwaraTool-Classic-*) ;;
    *) mv -- "$f" "IwaraTool-Classic-${f#IwaraTool-}" ;;
  esac
done
for f in IwaraTool-Flet-*; do
  mv -- "$f" "IwaraTool-${f#IwaraTool-Flet-}"
done
ls -l
