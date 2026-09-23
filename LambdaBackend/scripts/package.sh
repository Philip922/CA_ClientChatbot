#!/usr/bin/env bash
# Build cadre-lambda.zip.
#
# Dependencies are installed for the *Lambda* interpreter and platform, not the
# local one: a wheel built for the local Python or a local glibc will import
# fine here and fail in Lambda with a cryptic ELF error.
#
#   ./scripts/package.sh                    # python3.13, x86_64
#   PY_VERSION=3.12 ARCH=aarch64 ./scripts/package.sh
set -euo pipefail

PY_VERSION="${PY_VERSION:-3.13}"
ARCH="${ARCH:-x86_64}"

# Two manylinux tags, because packages disagree about which they publish:
# current numpy ships only manylinux_2_28 (and nothing at all for
# manylinux2014 on cp314), while older or smaller packages ship only
# manylinux2014. Lambda's AL2023 runtimes have glibc 2.34, so both are safe.
# pip picks the best match per package when several --platform flags are given.
PLATFORMS=("manylinux_2_28_${ARCH}" "manylinux2014_${ARCH}")

cd "$(dirname "$0")/.."
ROOT="$PWD"
BUILD="$ROOT/build"
ZIP="$ROOT/cadre-lambda.zip"

rm -rf "$BUILD" "$ZIP"
mkdir -p "$BUILD"

echo "==> Installing dependencies for python${PY_VERSION} / ${PLATFORMS[*]}"
platform_args=()
for plat in "${PLATFORMS[@]}"; do platform_args+=(--platform "$plat"); done

pip install \
  --requirement requirements.txt \
  --target "$BUILD" \
  --python-version "$PY_VERSION" \
  "${platform_args[@]}" \
  --implementation cp \
  --only-binary=:all: \
  --no-warn-conflicts \
  --quiet

echo "==> Copying application code"
cp handler.py app.py config.py run.sh system_prompts.md "$BUILD/"
cp -r orchestrator rag "$BUILD/"

if [[ -f rag_index.npz ]]; then
  cp rag_index.npz "$BUILD/"
  echo "    included rag_index.npz ($(du -h rag_index.npz | cut -f1))"
else
  echo "    WARNING: no rag_index.npz — query_knowledge_base will report the" >&2
  echo "             knowledge base as unavailable. Run scripts/build_index.py." >&2
fi

echo "==> Pruning"
find "$BUILD" -type d -name "__pycache__" -prune -exec rm -rf {} +
# Vendored test suites are dead weight in Lambda. Safe to match broadly: this
# project's own tests/ is never copied into the build.
find "$BUILD" -type d \( -name "tests" -o -name "test" \) -prune -exec rm -rf {} +
find "$BUILD" -name "*.pyc" -delete
rm -rf "$BUILD/bin"

UNZIPPED_KB=$(du -sk "$BUILD" | cut -f1)
echo "==> Zipping"
( cd "$BUILD" && zip -qr "$ZIP" . -x '*.dist-info/RECORD' )

printf '\nBuilt %s\n' "$ZIP"
printf '  zipped   %s\n' "$(du -h "$ZIP" | cut -f1)"
printf '  unzipped %s MB of Lambda'"'"'s 250 MB limit\n' "$((UNZIPPED_KB / 1024))"

if (( UNZIPPED_KB > 250 * 1024 )); then
  echo "ERROR: package exceeds Lambda's 250 MB unzipped limit." >&2
  exit 1
fi
