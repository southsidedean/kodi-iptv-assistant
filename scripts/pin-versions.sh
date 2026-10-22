#!/bin/sh
# Pin kia to the exact versions running now, or go back to following the latest.
#
#   scripts/pin-versions.sh          write today's versions into .env
#   scripts/pin-versions.sh --unpin  clear them again (follow the latest)
#
# Pins the base (Wolfi) and nginx images by digest and iptv-org/epg by commit. .env is backed
# up first. Apply with: docker compose up -d --build
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "No .env here; copy .env.example to .env first." >&2; exit 1; }

set_env() {  # set_env NAME VALUE: replace the NAME= line in .env, or add it
  if grep -q "^$1=" .env; then
    sed "s|^$1=.*|$1=$2|" .env > .env.tmp && mv .env.tmp .env
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}

cp .env ".env.bak-$(date +%Y%m%d-%H%M%S)"
if [ "${1:-}" = "--unpin" ]; then
  for name in KIA_BASE_IMAGE KIA_NGINX_IMAGE KIA_EPG_GIT_REF; do set_env "$name" ""; done
  echo "Unpinned: kia follows the latest versions again. Apply with: docker compose up -d --build"
  exit 0
fi

digest() {  # digest IMAGE: the image's repository digest, e.g. nginx@sha256:...
  docker image inspect --format '{{index .RepoDigests 0}}' "$1" 2>/dev/null || true
}
base_image=$(grep '^KIA_BASE_IMAGE=' .env | cut -d= -f2-); base_image=${base_image:-cgr.dev/chainguard/wolfi-base:latest}
nginx_image=$(grep '^KIA_NGINX_IMAGE=' .env | cut -d= -f2-); nginx_image=${nginx_image:-nginx:alpine}
base=$(digest "$base_image"); nginx=$(digest "$nginx_image")
epg=$(git -C vendor/epg rev-parse HEAD 2>/dev/null || true)
[ -n "$base" ] && set_env KIA_BASE_IMAGE "$base" && echo "KIA_BASE_IMAGE=$base"
[ -n "$nginx" ] && set_env KIA_NGINX_IMAGE "$nginx" && echo "KIA_NGINX_IMAGE=$nginx"
[ -n "$epg" ] && set_env KIA_EPG_GIT_REF "$epg" && echo "KIA_EPG_GIT_REF=$epg"
[ -n "$base$nginx$epg" ] || { echo "Nothing to pin yet: run kia once first." >&2; exit 1; }
echo "Pinned. Apply with: docker compose up -d --build"
