#!/bin/sh
# Pin kia to the exact versions running now, or go back to following the latest.
#
#   scripts/pin-versions.sh          write today's versions into .env
#   scripts/pin-versions.sh --unpin  clear them again (follow the latest)
#
# Pins the base (Wolfi) and nginx images by digest and iptv-org/epg by commit. .env is backed
# up first. Apply with: docker compose up -d --build
set -eu
# Ranges such as [a-z] mean only ASCII letters in the C locale; in others (macOS's sh, in
# UTF-8) they take capitals and accented letters too, and a name check lets them through.
LC_ALL=C
export LC_ALL
umask 077  # .env and its backups hold passwords: only you can read them
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "No .env here; copy .env.example to .env first." >&2; exit 1; }

set_env() {  # set_env NAME VALUE: replace the NAME= line in .env, or add it
  if grep -q "^$1=" .env; then
    K=$1 V=$2 awk 'BEGIN { FS = OFS = "=" } $1 == ENVIRON["K"] { print ENVIRON["K"] "=" ENVIRON["V"]; next } { print }' .env > .env.tmp && mv .env.tmp .env
  else
    if [ -s .env ] && [ -n "$(tail -c 1 .env)" ]; then echo >> .env; fi  # a last line without its line break
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}
# A setting's value as Compose reads it, without one pair of quotes around it.
get() { grep "^$1=" .env | tail -n 1 | cut -d= -f2- | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/" || true; }

cp .env ".env.bak-$(date +%Y%m%d-%H%M%S)"
if [ "${1:-}" = "--unpin" ]; then
  for name in KIA_BASE_IMAGE KIA_NGINX_IMAGE KIA_EPG_GIT_REF; do set_env "$name" ""; done
  echo "Unpinned: kia follows the latest versions again. Apply with: docker compose up -d --build"
  exit 0
fi

digest() {  # digest IMAGE: the image's repository digest, e.g. nginx@sha256:...
  docker image inspect --format '{{index .RepoDigests 0}}' "$1" 2>/dev/null || true
}
base_image=$(get KIA_BASE_IMAGE); base_image=${base_image:-cgr.dev/chainguard/wolfi-base:latest}
nginx_image=$(get KIA_NGINX_IMAGE); nginx_image=${nginx_image:-nginx:alpine}
base=$(digest "$base_image"); nginx=$(digest "$nginx_image")
case "$base_image" in *@sha256:*) ;; *)
  if [ -z "$base" ]; then
    # Docker's builder (BuildKit) doesn't keep the base image with the others, so it's often
    # not here to read. Pin the newest one instead, by its digest, and say so.
    # Its digest is the hash of its manifest, byte for byte: kept in a file, since $(...)
    # would drop a newline at its end. Nothing read, nothing pinned.
    manifest=$(mktemp)
    remote=""
    if docker buildx imagetools inspect --raw "$base_image" >"$manifest" 2>/dev/null && [ -s "$manifest" ]; then
      remote=$({ sha256sum "$manifest" 2>/dev/null || shasum -a 256 "$manifest"; } | cut -d' ' -f1)
    fi
    rm -f "$manifest"
    case "$remote" in
      # The tag is after the last ":" of the name's last part (my-server.lan:5000/x has a port, not a tag).
      [0-9a-f]*[0-9a-f]) case "${base_image##*/}" in *:*) base="${base_image%:*}@sha256:$remote" ;; *) base="$base_image@sha256:$remote" ;; esac
        echo "The base image kia was built on isn't kept on this computer, so this pins today's newest;"
        echo "docker compose up -d --build rebuilds kia on it." ;;
      *) echo "Couldn't find the base image's digest ($base_image), so it isn't pinned." >&2 ;;
    esac
  fi ;;
esac
# Only vendor/epg's own copy: in an empty folder, git would answer with kia's own commit.
epg=""
if [ -e vendor/epg/.git ]; then epg=$(git -C vendor/epg rev-parse HEAD 2>/dev/null || true); fi
[ -n "$base" ] && set_env KIA_BASE_IMAGE "$base" && echo "KIA_BASE_IMAGE=$base"
[ -n "$nginx" ] && set_env KIA_NGINX_IMAGE "$nginx" && echo "KIA_NGINX_IMAGE=$nginx"
[ -n "$epg" ] && set_env KIA_EPG_GIT_REF "$epg" && echo "KIA_EPG_GIT_REF=$epg"
[ -n "$base$nginx$epg" ] || { echo "Nothing to pin yet: run kia once first." >&2; exit 1; }
echo "Pinned. Apply with: docker compose up -d --build"
