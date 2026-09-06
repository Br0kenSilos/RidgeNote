#!/usr/bin/env bash
# Build (and optionally push) the RidgeNote production image.
#
# Usage:
#   scripts/build-image.sh <registry-image-name> [--push]
#
# Example:
#   scripts/build-image.sh registry.example.com/example/ridgenote
#   scripts/build-image.sh registry.example.com/example/ridgenote --push
#
# Requires a clean Git working tree. Builds for linux/amd64. Tags the
# resulting image with a commit-derived tag (sha-<short-commit>) and,
# if the repository HEAD is exactly tagged with an annotated release
# tag (vX.Y.Z), also with that release tag. Never pushes unless --push
# is passed explicitly. Never embeds credentials.

set -euo pipefail

IMAGE_NAME="${1:?usage: scripts/build-image.sh <registry-image-name> [--push]}"
PUSH=0
if [[ "${2:-}" == "--push" ]]; then
  PUSH=1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: working tree is not clean. Commit or stash changes before building." >&2
  git status --short >&2
  exit 1
fi

REVISION="$(git rev-parse HEAD)"
SHORT_REVISION="$(git rev-parse --short HEAD)"
CREATED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

RELEASE_TAG=""
if git describe --tags --exact-match >/dev/null 2>&1; then
  candidate="$(git describe --tags --exact-match)"
  if [[ "$candidate" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    RELEASE_TAG="${candidate#v}"
  fi
fi

COMMIT_REF="${IMAGE_NAME}:sha-${SHORT_REVISION}"

echo "Building ${COMMIT_REF}"
echo "  revision: ${REVISION}"
echo "  created:  ${CREATED}"

docker buildx build \
  --platform linux/amd64 \
  --build-arg "IMAGE_VERSION=${RELEASE_TAG:-0.0.0-${SHORT_REVISION}}" \
  --build-arg "IMAGE_REVISION=${REVISION}" \
  --build-arg "IMAGE_CREATED=${CREATED}" \
  -t "${COMMIT_REF}" \
  --load \
  .

if [[ -n "$RELEASE_TAG" ]]; then
  RELEASE_REF="${IMAGE_NAME}:${RELEASE_TAG}"
  docker tag "${COMMIT_REF}" "${RELEASE_REF}"
  echo "Also tagged: ${RELEASE_REF}"
fi

echo ""
echo "Built references:"
echo "  ${COMMIT_REF}"
[[ -n "$RELEASE_TAG" ]] && echo "  ${IMAGE_NAME}:${RELEASE_TAG}"

if [[ "$PUSH" -eq 1 ]]; then
  echo ""
  echo "Pushing ${COMMIT_REF}"
  docker push "${COMMIT_REF}"
  if [[ -n "$RELEASE_TAG" ]]; then
    echo "Pushing ${IMAGE_NAME}:${RELEASE_TAG}"
    docker push "${IMAGE_NAME}:${RELEASE_TAG}"
  fi
  echo ""
  echo "Published. Record the digest with:"
  echo "  docker inspect --format='{{index .RepoDigests 0}}' ${COMMIT_REF}"
else
  echo ""
  echo "Not pushed (no --push flag). To push after a manual 'docker login':"
  echo "  scripts/build-image.sh ${IMAGE_NAME} --push"
fi
