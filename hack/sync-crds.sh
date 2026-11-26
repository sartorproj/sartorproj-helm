#!/usr/bin/env bash
# Copy the CRDs from a sartorproj/sartor checkout into files/crds.
# Usage: hack/sync-crds.sh [path-to-sartor-checkout]   (default: ../sartor)
set -euo pipefail

src="${1:-../sartor}/config/crd/bases"
dst="$(cd "$(dirname "$0")/.." && pwd)/files/crds"

if [ ! -d "$src" ]; then
  echo "CRD source $src not found" >&2
  exit 1
fi

rm -f "$dst"/*.yaml
cp "$src"/*.yaml "$dst"/
echo "synced $(ls "$dst" | wc -l) CRDs from $src"
