#!/bin/sh
# Update kia to its newest release (or another version), then rebuild and restart it.
#
#   scripts/update.sh              the newest release
#   scripts/update.sh v0.5.2       that release
#   scripts/update.sh --main       what's merged since the last release (not released yet)
#   scripts/update.sh --no-docker  for kia without Docker: restarts the service instead
#   scripts/update.sh --no-restart only move the files to the release (a new copy, before setup)
#   scripts/update.sh --yes        don't ask, even if someone is watching
#
# Read the changelog's "Upgrading" notes for the new version first: some need a step.
set -eu

cd "$(dirname "$0")/.."
target="" docker=yes ask=yes restart=yes
while [ $# -gt 0 ]; do
    case "$1" in
        --main) target=main ;;
        --no-docker) docker=no ;;
        --no-restart) restart=no ;;
        --yes | -y) ask=no ;;
        -h | --help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        v[0-9]*) target=$1 ;;
        *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
    shift
done
die() { echo "update: $*" >&2; exit 1; }

[ -d .git ] || die "this isn't a git copy of kia; download it again (docs/install.md)."
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    git status --short --untracked-files=no
    die "kia's own files above were changed here. Move your changes to sources.local.txt or .env (or run git stash), then try again."
fi

echo "Looking for kia's releases..."
git fetch --quiet --tags --force origin
before=$(git describe --tags --always)
before_rev=$(git rev-parse HEAD)
if [ "$target" = main ]; then
    git checkout --quiet main 2>/dev/null || git checkout --quiet -b main --track origin/main
    git pull --quiet --ff-only origin main
else
    if [ -z "$target" ]; then
        # The newest release: version tags without a suffix (no -rc1).
        target=$(git tag --list 'v[0-9]*' --sort=-version:refname | grep -v -- '-' | head -n 1)
        [ -n "$target" ] || die "no releases found."
    fi
    git rev-parse --quiet --verify "refs/tags/$target" >/dev/null || die "there's no release $target."
    git -c advice.detachedHead=false checkout --quiet "$target"
fi
after=$(git describe --tags --always)
if [ "$before" = "$after" ]; then
    echo "kia is already at $after."
else
    echo "kia: $before -> $after. Read the changelog's \"Upgrading\" notes for it: CHANGELOG.md"
fi

[ "$restart" = yes ] || exit 0

# Nobody watching? A restart cuts off Pluto TV through kia, and Plex's Live TV through Threadfin.
port=$(grep '^KIA_PORT=' .env 2>/dev/null | cut -d= -f2-)
watching=$(curl -fsS --max-time 5 "http://localhost:${port:-8780}/metrics" 2>/dev/null | awk '
    /^kia_pluto_channels_playing / && $2 > 0 { n += $2 }
    /^kia_plex_sessions\{/ && $0 !~ /kind="none"/ { n += $NF }
    /^kia_refresh_running 1/ { r = 1 }
    END { if (n > 0) print n " playing"; else if (r) print "a refresh is running" }')
if [ -n "$watching" ] && [ "$ask" = yes ]; then
    printf 'kia says: %s. Restart now anyway? [y/N] ' "$watching"
    { read -r answer </dev/tty; } 2>/dev/null || answer=n  # no terminal: don't restart
    case "$answer" in [yY]*) ;; *) echo "Not restarted. Run this again later, or with --yes."; exit 0 ;; esac
fi

if [ "$docker" = no ]; then
    exec scripts/install-service.sh
fi
if grep -q '^KIA_IMAGE=.' .env 2>/dev/null; then
    docker compose pull
else
    docker compose build --pull
    docker compose pull --ignore-buildable
fi
docker compose up -d
# Docker pins a config file mounted on its own (nginx's, Prometheus's) to the copy the
# container started with, so after a release that changed one, that container restarts.
changed=$(git diff --name-only "$before_rev" HEAD -- deploy/ 2>/dev/null || true)
restart=""
if echo "$changed" | grep -q -E '^deploy/(nginx\.conf|kia-common\.inc|kia-api\.inc|nginx-tls\.sh)$'; then restart="web"; fi
if echo "$changed" | grep -q '^deploy/monitoring/prometheus\.yml$'; then restart="$restart prometheus"; fi
if [ -n "$restart" ]; then
    running=$(docker compose ps --services --status running 2>/dev/null || true)
    for service in $restart; do
        if echo "$running" | grep -qx "$service"; then
            docker compose restart "$service" && echo "Restarted $service, for its new settings."
        fi
    done
fi
echo "Done: kia $(git describe --tags --always) is running."
