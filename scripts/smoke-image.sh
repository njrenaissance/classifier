#!/usr/bin/env bash
# Smoke-check a built classifier image. Single source of truth for the image
# acceptance checks, shared by docker-build.yml (PR gate) and docker-publish.yml
# (pre-push), so the two cannot drift.
#
# Usage: scripts/smoke-image.sh <image-ref>
set -euo pipefail

IMAGE="${1:?usage: scripts/smoke-image.sh <image-ref>}"

echo "smoke: walker entry point loads"
docker run --rm "$IMAGE" python -m walker --help >/dev/null

echo "smoke: processor entry point loads"
docker run --rm "$IMAGE" python -m processor --help >/dev/null

echo "smoke: category file is present"
# Wrapped in `sh -c` so the container-absolute path isn't rewritten by MSYS/Git
# Bash path conversion when this script is run from Windows.
docker run --rm "$IMAGE" sh -c 'test -f /app/categories.md'

echo "smoke: dev dependencies are excluded"
docker run --rm "$IMAGE" python -c \
  "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pytest') is None else 1)"

echo "smoke: migration tooling is runnable"
docker run --rm "$IMAGE" alembic --help >/dev/null

echo "smoke: all checks passed for $IMAGE"
