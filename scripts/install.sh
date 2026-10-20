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
#   --port N        port for playlists and the status page (default 8780)
#   --https-port N  port for the secure status page (default 8743)
#   --yes           don't ask; accept what it finds
set -eu

REPO=https://github.com/southsidedean/kodi-iptv-assistant.git
DIR=""
ADDRESS=""
TZ_NAME=""
PORT=8780
HTTPS_PORT=8743
YES=0

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
        --yes|-y) YES=1; shift ;;
        -h|--help) say "Options: --dir PATH, --address IP, --tz ZONE, --port N, --https-port N, --yes. See the top of scripts/install.sh."; exit 0 ;;
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
    *) die "this installer is for macOS and Linux; see docs/install.md for other options" ;;
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
    say "Downloaded kia to $DIR"
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
    sed -e "s|^KIA_PUBLIC_URL=.*|KIA_PUBLIC_URL=http://$ADDRESS:$PORT|" \
        -e "s|^KIA_BIND=.*|KIA_BIND=$bind|" \
        -e "s|^KIA_PORT=.*|KIA_PORT=$PORT|" \
        -e "s|^KIA_HTTPS_PORT=.*|KIA_HTTPS_PORT=$HTTPS_PORT|" \
        -e "s|^TZ=.*|TZ=$TZ_NAME|" \
        -e "s|^PUID=.*|PUID=$(id -u)|" \
        -e "s|^PGID=.*|PGID=$(id -g)|" \
        .env.example >.env
    say "Made .env: address http://$ADDRESS:$PORT, time zone $TZ_NAME."
fi
# The folders kia writes; made here so Docker doesn't create them owned by root.
mkdir -p output state work vendor profiles tls

if [ "$OS" = Linux ] && command -v ufw >/dev/null 2>&1; then
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
say "Status page:          $url/"
say "Secure status page:   https://$host:${https_port:-8743}/   (for making changes; your browser warns about kia's own certificate the first time)"
if [ -n "$password" ]; then
    say "Password for changes: $password   (also in $DIR/state/admin-password.txt)"
fi
say "Playlist for players: $url/curated.m3u"
say ""
say "Next: set up Kodi or another player on each device: docs/kodi.md, or docs/players.md for the others."
