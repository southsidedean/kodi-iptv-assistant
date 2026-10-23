#!/bin/sh
# Install kia with Docker on macOS or Linux, step by step.
#
#   curl -fsSL https://raw.githubusercontent.com/southsidedean/kodi-iptv-assistant/main/scripts/install.sh | sh
#   or, in a copy you've downloaded:  scripts/install.sh
#
# It checks for Docker and Git, downloads kia (to ~/kodi-iptv-assistant unless you run it
# inside a copy), makes .env with this computer's address and time zone, and starts kia.
# Running it again is safe: it keeps your .env and settings.
#
# Options (all optional):
#   --dir PATH      where to put kia (default: this copy, or ~/kodi-iptv-assistant)
#   --address IP    the address other devices use to reach this computer
#   --tz ZONE       time zone, e.g. America/New_York
#   --port N        port for playlists and the Dashboard (default 8780)
#   --https-port N  port for the secure Dashboard (default 8743)
#   --main          follow what's merged since the last release, instead of the release
#   --yes           don't ask; accept what it finds
set -eu

REPO=https://github.com/southsidedean/kodi-iptv-assistant.git
DIR=""
ADDRESS=""
TZ_NAME=""
PORT=8780
HTTPS_PORT=8743
YES=0
MAIN=no

say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
die() { printf 'Stopped: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --dir) DIR=$2; shift 2 ;;
        --address) ADDRESS=$2; shift 2 ;;
        --tz) TZ_NAME=$2; shift 2 ;;
        --port) PORT=$2; shift 2 ;;
        --https-port) HTTPS_PORT=$2; shift 2 ;;
        --main) MAIN=yes; shift ;;
        --yes|-y) YES=1; shift ;;
        -h|--help) say "Options: --dir PATH, --address IP, --tz ZONE, --port N, --https-port N, --main, --yes. See the top of scripts/install.sh."; exit 0 ;;
        *) die "unknown option $1 (try --help)" ;;
    esac
done

# Ask when someone's at the keyboard (also when piped from curl); otherwise take the default.
ask() {  # ask "Question" default -> answer
    if [ "$YES" = 1 ] || ! [ -r /dev/tty ]; then
        printf '%s\n' "$2"
        return
    fi
    printf '%s [%s]: ' "$1" "$2" >/dev/tty
    read -r reply </dev/tty || reply=""
    printf '%s\n' "${reply:-$2}"
}

OS=$(uname -s)
case "$OS" in
    Darwin|Linux) ;;
    *) die "kia runs on macOS and Linux (including Raspberry Pi), not Windows yet. Kodi on Windows works with a kia on another computer." ;;
esac

step "Checking what's installed"
if ! command -v docker >/dev/null 2>&1; then
    if [ "$OS" = Darwin ]; then
        die "Docker isn't installed. Install Docker Desktop from https://www.docker.com/products/docker-desktop/, start it, then run this again."
    fi
    die "Docker isn't installed. Install it with:  curl -fsSL https://get.docker.com | sudo sh  then  sudo usermod -aG docker \$USER  then log out and back in, and run this again."
fi
if ! docker compose version >/dev/null 2>&1; then
    die "Docker is installed but not working (or Docker Compose is missing). Start Docker Desktop (macOS), or check that your user is in the docker group (Linux: log out and back in after usermod)."
fi
command -v git >/dev/null 2>&1 || die "Git isn't installed. macOS: run  git --version  and accept the offer to install it. Linux: sudo apt install git (or your system's equivalent)."
say "Docker and Git are ready."

step "Getting kia"
here=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd) || here=""
if [ -z "$DIR" ] && [ -n "$here" ] && [ -f "$here/compose.yaml" ] && [ -f "$here/src/kia/cli.py" ]; then
    DIR=$here
fi
DIR=${DIR:-$HOME/kodi-iptv-assistant}
if [ -d "$DIR/.git" ]; then
    say "Using kia in $DIR"
elif [ -e "$DIR" ]; then
    die "$DIR exists but isn't a copy of kia. Use --dir to pick another folder."
else
    git clone --quiet "$REPO" "$DIR"
    if [ "$MAIN" = no ]; then
        # The newest release (version tags without a suffix such as -rc1), not what's
        # merged since: scripts/update.sh moves to the next one.
        release=$(git -C "$DIR" tag --list 'v[0-9]*' --sort=-version:refname | grep -v -- '-' | head -n 1)
        [ -n "$release" ] && git -C "$DIR" -c advice.detachedHead=false checkout --quiet "$release"
    fi
    say "Downloaded kia $(git -C "$DIR" describe --tags --always) to $DIR"
fi
cd "$DIR"

step "Settings"
if [ -f .env ]; then
    say "Keeping your existing .env. (Delete it and run this again to start over.)"
else
    if [ -z "$ADDRESS" ]; then
        if [ "$OS" = Darwin ]; then
            ADDRESS=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
        else
            ADDRESS=$(hostname -I 2>/dev/null | awk '{print $1}')
        fi
        ADDRESS=$(ask "This computer's address on your network (for other devices)" "${ADDRESS:-localhost}")
    fi
    if [ -z "$TZ_NAME" ]; then
        if [ "$OS" = Darwin ]; then
            TZ_NAME=$(readlink /etc/localtime 2>/dev/null | sed 's|.*zoneinfo/||')
        else
            TZ_NAME=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || true)
        fi
        TZ_NAME=$(ask "Your time zone" "${TZ_NAME:-Etc/UTC}")
    fi
    bind=0.0.0.0
    if [ "$ADDRESS" = localhost ] || [ "$ADDRESS" = 127.0.0.1 ]; then
        bind=127.0.0.1  # only this computer
    fi
    # .env holds kia's password and other secrets: readable by you only, from the start.
    (umask 077 && sed -e "s|^KIA_PUBLIC_URL=.*|KIA_PUBLIC_URL=http://$ADDRESS:$PORT|" \
        -e "s|^KIA_BIND=.*|KIA_BIND=$bind|" \
        -e "s|^KIA_PORT=.*|KIA_PORT=$PORT|" \
        -e "s|^KIA_HTTPS_PORT=.*|KIA_HTTPS_PORT=$HTTPS_PORT|" \
        -e "s|^TZ=.*|TZ=$TZ_NAME|" \
        -e "s|^PUID=.*|PUID=$(id -u)|" \
        -e "s|^PGID=.*|PGID=$(id -g)|" \
        .env.example >.env)
    say "Made .env: address http://$ADDRESS:$PORT, time zone $TZ_NAME."
    # A Mac: Plex's and Grafana's databases in Docker volumes, since a folder shared from
    # the Mac into Docker's Linux machine breaks Plex's.
    if [ "$OS" = Darwin ]; then
        (umask 077 && sed -e "s|^KIA_PLEX_CONFIG=.*|KIA_PLEX_CONFIG=plex-config|" \
            -e "s|^KIA_GRAFANA_DATA=.*|KIA_GRAFANA_DATA=grafana-data|" .env >.env.tmp) && mv .env.tmp .env
        say "On a Mac, Plex's and Grafana's data go in Docker volumes (docs/configuration.md#on-a-mac)."
    fi
    # A Raspberry Pi, or any computer with less than 6 GB of memory: settings that keep
    # the first refresh from running out of memory (docs/setups.md#raspberry-pi).
    memory_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
    if grep -qs "Raspberry Pi" /proc/device-tree/model || { [ "${memory_kb:-0}" -gt 0 ] && [ "$memory_kb" -lt 6000000 ]; }; then
        (umask 077 && sed -e "s|^KIA_PARALLEL_SITES=.*|KIA_PARALLEL_SITES=1|" -e "s|^KIA_CHECK_CONCURRENCY=.*|KIA_CHECK_CONCURRENCY=8|" \
            -e "s|^KIA_MEMORY_LIMIT=.*|KIA_MEMORY_LIMIT=2g|" .env >.env.tmp) && mv .env.tmp .env
        say "This computer has little memory (or is a Raspberry Pi), so .env uses the lighter settings from docs/setups.md#raspberry-pi."
    fi
fi
chmod 600 .env  # also an .env from before kia did this
# The folders kia writes; made here so Docker doesn't create them owned by root.
mkdir -p output state work vendor profiles tls

# kia for this computer only (KIA_BIND=127.0.0.1) needs no ports opened.
if [ "$OS" = Linux ] && command -v ufw >/dev/null 2>&1 && ! grep -q '^KIA_BIND=127\.0\.0\.1' .env; then
    step "Firewall"
    if sudo -n true 2>/dev/null; then
        if sudo ufw status 2>/dev/null | grep -q "Status: active"; then
            sudo ufw allow "$PORT/tcp" >/dev/null && sudo ufw allow "$HTTPS_PORT/tcp" >/dev/null
            say "Opened ports $PORT and $HTTPS_PORT in ufw so other devices can reach kia."
        else
            say "ufw is installed but off; nothing to do."
        fi
    else
        say "If ufw is on, let other devices in with:  sudo ufw allow $PORT/tcp && sudo ufw allow $HTTPS_PORT/tcp"
    fi
fi

step "Starting kia (the first build takes a few minutes)"
docker compose up -d --build

url=$(sed -n 's/^KIA_PUBLIC_URL=//p' .env)
host=$(printf '%s' "$url" | sed -e 's|^[a-z]*://||' -e 's|:[0-9]*$||' -e 's|/.*||')
https_port=$(sed -n 's/^KIA_HTTPS_PORT=//p' .env)
password=""
i=0
while [ $i -lt 30 ] && ! [ -s state/admin-password.txt ] && ! grep -q '^KIA_ADMIN_PASSWORD=.' .env; do
    sleep 2
    i=$((i + 1))
done
[ -s state/admin-password.txt ] && password=$(cat state/admin-password.txt)

step "Done"
say "kia is running its first refresh now, which takes up to an hour. Watch it with:"
say "  cd $DIR && docker compose logs -f kia"
say ""
say "Dashboard:          $url/"
say "Secure Dashboard:   https://$host:${https_port:-8743}/   (for making changes; your browser warns about kia's own certificate the first time)"
if [ -n "$password" ]; then
    say "Password for changes: $password   (also in $DIR/state/admin-password.txt)"
fi
say "Playlist for players: $url/curated.m3u"
say ""
say "Next: wait for the first refresh (the Dashboard says when it's done), then set up"
say "Kodi or another player on each device:"
say "  https://github.com/southsidedean/kodi-iptv-assistant/blob/main/docs/kodi.md"
say "  https://github.com/southsidedean/kodi-iptv-assistant/blob/main/docs/players.md (other players)"
