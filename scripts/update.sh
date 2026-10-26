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
get() { grep "^$1=" .env 2>/dev/null | tail -n 1 | cut -d= -f2- || true; }
set_env() {  # set_env NAME VALUE: replace the line
    (umask 077 && awk -v k="$1" -v v="$2" 'BEGIN { FS = OFS = "=" } $1 == k { print k "=" v; next } { print }' .env >.env.tmp) && mv .env.tmp .env
}

[ -d .git ] || die "this isn't a git copy of kia; download it again (docs/install.md)."
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    git status --short --untracked-files=no
    die "kia's own files above were changed here. Move your changes to sources.local.txt or .env (or run git stash), then try again."
fi

echo "Looking for kia's releases..."
git fetch --quiet --tags --force origin
before=$(git describe --tags --always)
# What the containers last started with, kept until they've restarted, so a run that stops
# before that (Docker not running, a failed build) isn't forgotten. It's kept in git's own
# folder, which the kia container can't write (it sees the kia folder read-only), and read
# back only if it's a commit: anything else would reach git as an option.
deployed=$(git rev-parse --git-dir)/kia-deployed-rev
commit_or_empty() {  # the commit named in file $1, or nothing
    rev=$(head -c 100 "$1" 2>/dev/null | tr -d '[:space:]' || true)
    case "$rev" in "" | *[!0-9a-f]*) return 0 ;; esac
    if git cat-file -e "$rev^{commit}" 2>/dev/null; then echo "$rev"; fi
}
if [ ! -s "$deployed" ] && [ -f state/deployed-rev ] && [ ! -L state/deployed-rev ]; then
    # Where kia 0.5.5 kept it, which the kia container can write: taken once, then removed.
    old=$(commit_or_empty state/deployed-rev)
    if [ -n "$old" ]; then echo "$old" >"$deployed"; fi
fi
# -r, so a folder kia's container made there can't stop every update (rm -r doesn't follow links).
rm -rf state/deployed-rev
if [ "$restart" = yes ] && [ ! -s "$deployed" ]; then
    git rev-parse HEAD >"$deployed"
fi
before_rev=$(commit_or_empty "$deployed")
before_rev=${before_rev:-$(git rev-parse HEAD)}
# A copy that follows main (set up with --main) keeps following it. The newest release is
# older than main, so moving there would undo what main has, without a word; name a
# release for that.
if [ -z "$target" ] && [ "$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)" = main ]; then
    echo "This copy follows main, so it stays on main (to go back to a release, name it: scripts/update.sh vX.Y.Z)."
    target=main
fi
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

# Nobody watching? A restart cuts off Pluto TV through kia, and Live TV through Threadfin
# (Plex) or kia's playlist (Jellyfin).
port=$(get KIA_PORT)
if numbers=$(curl -fsS --max-time 5 "http://localhost:${port:-8780}/metrics" 2>/dev/null); then
    watching=$(printf '%s\n' "$numbers" | awk '
        /^kia_pluto_channels_playing / && $2 > 0 { n += $2 }
        /^kia_(plex|jellyfin)_sessions\{/ && $0 !~ /kind="none"/ { n += $NF }
        /^kia_refresh_running 1/ { r = 1 }
        END { if (n > 0) print n " playing"; else if (r) print "a refresh is running" }')
elif [ "$docker" = yes ] && ! docker compose ps --services --status running 2>/dev/null | grep -qx kia; then
    watching=""  # kia isn't running, so nobody is watching through it
else
    # kia runs but didn't answer (busy, or a port that isn't KIA_PORT): that's not the
    # same as nobody watching.
    watching="no answer from kia's numbers, so it can't tell who's watching"
fi
if [ -n "$watching" ] && [ "$ask" = yes ]; then
    printf 'kia says: %s. Restart now anyway? [y/N] ' "$watching"
    { read -r answer </dev/tty; } 2>/dev/null || answer=n  # no terminal: don't restart
    case "$answer" in [yY]*) ;; *) echo "Not restarted. Run this again later, or with --yes."; exit 0 ;; esac
fi

if [ "$docker" = no ]; then
    exec scripts/install-service.sh
fi
image=$(get KIA_IMAGE)
if [ -n "$image" ]; then
    # A published image with a version: the one for this release, to match its files. The
    # version is after the last ":" of the name's last part: my-server.lan:5000/kia has a
    # port, not a version. An image pinned by its digest (@sha256:...) stays as you set it.
    name=${image##*/} tag=""
    case "$name" in *:*) tag=${name##*:} ;; esac
    case "$image:$tag" in
        *@*) echo "KIA_IMAGE is pinned by its digest, so it stays as it is; change it to this release's yourself." ;;
        *:latest | *:main | *:) ;;
        *:[0-9]*)
            if [ "$target" = main ]; then
                echo "KIA_IMAGE=$image is a release; the files are main's. For main, build kia here (empty KIA_IMAGE)."
            else
                set_env KIA_IMAGE "${image%:*}:${target#v}"
                echo "KIA_IMAGE is now ${image%:*}:${target#v}, to match."
            fi ;;
    esac
    docker compose pull
else
    docker compose build --pull
    docker compose pull --ignore-buildable
fi
docker compose up -d
# Docker pins a config file mounted on its own (nginx's, Prometheus's) to the copy the
# container started with, so after a release that changed one, that container restarts.
changed=$(git diff --name-only "$before_rev" HEAD -- deploy/ scripts/stack.sh 2>/dev/null || true)
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
# The kia-stack service keeps running the stack.sh it started with: restart it for the new one.
# On a Mac, launchd also keeps the service's settings file as installed (where its log goes,
# say), so a new one is installed again rather than only restarted.
if echo "$changed" | grep -qx -e 'scripts/stack.sh' -e 'deploy/kia-stack.plist'; then
    if [ "$(uname -s)" = Darwin ]; then
        if launchctl print "gui/$(id -u)/io.github.southsidedean.kia-stack" >/dev/null 2>&1; then
            if echo "$changed" | grep -qx 'deploy/kia-stack.plist'; then
                scripts/install-service.sh --stack >/dev/null && echo "Installed the kia-stack service again, for its new settings."
            elif launchctl kickstart -k "gui/$(id -u)/io.github.southsidedean.kia-stack" 2>/dev/null; then
                echo "Restarted the kia-stack service, for its new version."
            fi
        fi
    elif systemctl --user is-active --quiet kia-stack 2>/dev/null; then
        systemctl --user restart kia-stack && echo "Restarted the kia-stack service, for its new version."
    fi
fi
git rev-parse HEAD >"$deployed"
echo "Done: kia $(git describe --tags --always) is running."
