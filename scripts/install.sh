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
#   --preset NAME   what this kia is for: kodi, plex-box, plex-elsewhere, jellyfin-box, or
#                   everything (stacks/, docs/stack.md); without it, it asks (default kodi)
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
PRESET=""

say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
die() { printf 'Stopped: %s\n' "$*" >&2; exit 1; }
value() { [ $# -ge 2 ] || die "$1 needs a value (try --help)"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --dir) value "$@"; DIR=$2; shift 2 ;;
        --address) value "$@"; ADDRESS=$2; shift 2 ;;
        --tz) value "$@"; TZ_NAME=$2; shift 2 ;;
        --port) value "$@"; PORT=$2; shift 2 ;;
        --https-port) value "$@"; HTTPS_PORT=$2; shift 2 ;;
        --preset) value "$@"; PRESET=$2; shift 2 ;;
        --main) MAIN=yes; shift ;;
        --yes|-y) YES=1; shift ;;
        -h|--help) say "Options: --dir PATH, --address IP, --tz ZONE, --port N, --https-port N, --preset NAME, --main, --yes. See the top of scripts/install.sh."; exit 0 ;;
        *) die "unknown option $1 (try --help)" ;;
    esac
done
for n in "$PORT" "$HTTPS_PORT"; do
    case "$n" in '' | *[!0-9]*) die "a port is a number, such as 8780; not $n" ;; esac
done

# Ask when someone's at the keyboard (also when piped from curl); otherwise take the default.
# /dev/tty is there even without a terminal (ssh without -t, cron), so try opening it.
ask() {  # ask "Question" default -> answer
    if [ "$YES" = 1 ] || ! (: </dev/tty >/dev/tty) 2>/dev/null; then
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
    die "Docker Compose is missing. Docker Desktop (macOS) has it; on Linux, install Docker again with  curl -fsSL https://get.docker.com | sudo sh"
fi
# docker compose version doesn't need Docker running; docker info does.
if ! docker info >/dev/null 2>&1; then
    if [ "$OS" = Darwin ]; then
        die "Docker isn't running. Start Docker Desktop, wait until it says it's running, then run this again."
    fi
    die "Docker isn't answering. Start it with  sudo systemctl start docker ; if you just ran  sudo usermod -aG docker \$USER , log out and back in first. Then run this again."
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
if [ -n "$PRESET" ] && [ ! -f "stacks/$PRESET.env" ]; then
    die "there's no preset $PRESET; choose one of: $(for f in stacks/*.env; do basename "$f" .env; done | tr '\n' ' ')"
fi

step "Settings"
new_env=no
if [ -f .env ]; then
    say "Keeping your existing .env. (Delete it and run this again to start over.)"
else
    if [ -z "$ADDRESS" ]; then
        # The address of the network connection the computer uses to reach the internet.
        if [ "$OS" = Darwin ]; then
            iface=$(route -n get default 2>/dev/null | awk '/interface:/ {print $2}')
            ADDRESS=$( { [ -n "$iface" ] && ipconfig getifaddr "$iface"; } 2>/dev/null || ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
        else
            ADDRESS=$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") {print $(i + 1); exit}}')
            [ -n "$ADDRESS" ] || ADDRESS=$(hostname -I 2>/dev/null | awk '{print $1}')
        fi
        [ -n "$ADDRESS" ] || say "Couldn't find this computer's address; with localhost, only this computer can use kia."
        ADDRESS=$(ask "This computer's address on your network (for other devices)" "${ADDRESS:-localhost}")
    fi
    # Just the address: http://, a port, or a / typed with it come off.
    ADDRESS=$(printf '%s' "$ADDRESS" | sed -e 's|^[A-Za-z]*://||' -e 's|/.*||')
    case "$ADDRESS" in *:*:*) ;; *) ADDRESS=${ADDRESS%%:*} ;; esac
    case "$ADDRESS" in '' | *[!A-Za-z0-9.:_[\]-]*) die "that address ($ADDRESS) has characters an address can't have; use one such as 192.168.1.20 or my-server.lan" ;; esac
    if [ -z "$TZ_NAME" ]; then
        if [ "$OS" = Darwin ]; then
            TZ_NAME=$(readlink /etc/localtime 2>/dev/null | sed 's|.*zoneinfo/||')
        else
            TZ_NAME=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || true)
        fi
        TZ_NAME=$(ask "Your time zone" "${TZ_NAME:-Etc/UTC}")
    fi
    case "$TZ_NAME" in '' | *[!A-Za-z0-9/_+-]*) die "that time zone ($TZ_NAME) isn't one; use a name such as America/New_York" ;; esac
    bind=0.0.0.0
    if [ "$ADDRESS" = localhost ] || [ "$ADDRESS" = 127.0.0.1 ]; then
        bind=127.0.0.1  # only this computer
    fi
    # .env holds kia's password and other secrets: readable by you only, from the start.
    # Made as .env.new and moved into place when it's complete, so a stop half-way leaves none.
    (umask 077 && sed -e "s|^KIA_PUBLIC_URL=.*|KIA_PUBLIC_URL=http://$ADDRESS:$PORT|" \
        -e "s|^KIA_BIND=.*|KIA_BIND=$bind|" \
        -e "s|^KIA_PORT=.*|KIA_PORT=$PORT|" \
        -e "s|^KIA_HTTPS_PORT=.*|KIA_HTTPS_PORT=$HTTPS_PORT|" \
        -e "s|^TZ=.*|TZ=$TZ_NAME|" \
        -e "s|^PUID=.*|PUID=$(id -u)|" \
        -e "s|^PGID=.*|PGID=$(id -g)|" \
        .env.example >.env.new)
    # A Mac: Plex's, Jellyfin's, and Grafana's databases in Docker volumes, since a folder
    # shared from the Mac into Docker's Linux machine breaks Plex's.
    if [ "$OS" = Darwin ]; then
        (umask 077 && sed -e "s|^KIA_PLEX_CONFIG=.*|KIA_PLEX_CONFIG=plex-config|" \
            -e "s|^KIA_JELLYFIN_CONFIG=.*|KIA_JELLYFIN_CONFIG=jellyfin-config|" \
            -e "s|^KIA_GRAFANA_DATA=.*|KIA_GRAFANA_DATA=grafana-data|" .env.new >.env.tmp) && mv .env.tmp .env.new
        say "On a Mac, Plex's, Jellyfin's, and Grafana's data go in Docker volumes (docs/configuration.md#on-a-mac)."
    fi
    # A Raspberry Pi, or any computer with less than 6 GB of memory: settings that keep
    # the first refresh from running out of memory (docs/setups.md#raspberry-pi).
    if [ "$OS" = Darwin ]; then memory_kb=$(($(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1024)); else memory_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0); fi
    if grep -qs "Raspberry Pi" /proc/device-tree/model || { [ "${memory_kb:-0}" -gt 0 ] && [ "$memory_kb" -lt 6000000 ]; }; then
        (umask 077 && sed -e "s|^KIA_PARALLEL_SITES=.*|KIA_PARALLEL_SITES=1|" -e "s|^KIA_CHECK_CONCURRENCY=.*|KIA_CHECK_CONCURRENCY=8|" \
            -e "s|^KIA_MEMORY_LIMIT=.*|KIA_MEMORY_LIMIT=2g|" .env.new >.env.tmp) && mv .env.tmp .env.new
        say "This computer has little memory (or is a Raspberry Pi), so .env uses the lighter settings from docs/setups.md#raspberry-pi."
    fi
    if [ -z "$PRESET" ]; then
        say "What is this kia for? (docs/stack.md)"
        for f in stacks/*.env; do say "  $(basename "$f" .env): $(sed -n '1s/^# *//p' "$f")"; done
        PRESET=$(ask "Choose one" kodi)
        [ -f "stacks/$PRESET.env" ] || die "there's no preset $PRESET; run this again and choose one of the names above"
    fi
    mv .env.new .env
    new_env=yes
    say "Made .env: address http://$ADDRESS:$PORT, time zone $TZ_NAME."
fi
if [ -n "$PRESET" ]; then
    stack=$(sed -n 's/^KIA_STACK=//p' "stacks/$PRESET.env")
    address=$(sed -n 's|^KIA_PUBLIC_URL=http://||p' .env | sed 's|:[0-9]*$||')
    if grep -q '^KIA_STACK=' .env; then
        (umask 077 && sed -e "s|^KIA_STACK=.*|KIA_STACK=$stack|" .env >.env.tmp) && mv .env.tmp .env
    else
        printf 'KIA_STACK=%s\n' "$stack" >>.env  # an .env from before KIA_STACK
    fi
    # The addresses Plex and Jellyfin apps on your network use, from this computer's.
    case ",$stack," in *,plex,*) (umask 077 && sed -e "s|^KIA_PLEX_ADVERTISE=$|KIA_PLEX_ADVERTISE=http://$address:32400/|" .env >.env.tmp) && mv .env.tmp .env ;; esac
    case ",$stack," in *,jellyfin,*) (umask 077 && sed -e "s|^KIA_JELLYFIN_ADVERTISE=$|KIA_JELLYFIN_ADVERTISE=http://$address:8096|" .env >.env.tmp) && mv .env.tmp .env ;; esac
    say "Preset $PRESET: KIA_STACK=${stack:-(kia and its web server only)}."
fi
chmod 600 .env  # also an .env from before kia did this
# The folders kia writes; made here so Docker doesn't create them owned by root.
mkdir -p output state work vendor profiles tls

# kia for this computer only (KIA_BIND=127.0.0.1) needs no ports opened. The ports are
# .env's: on a second run, they may not be the ones given here.
PORT=$(sed -n 's/^KIA_PORT=//p' .env | tail -n 1); PORT=${PORT:-8780}
HTTPS_PORT=$(sed -n 's/^KIA_HTTPS_PORT=//p' .env | tail -n 1); HTTPS_PORT=${HTTPS_PORT:-8743}
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

stack=$(sed -n 's/^KIA_STACK=//p' .env)
plex_next=no apply_now=no
case ",$stack," in
    ,,) ;;
    # Threadfin needs kia's plex playlist, made on the Dashboard after the first refresh, and
    # Plex's first start a claim code, which works for 4 minutes: those are yours to do.
    *,plex,* | *,plex-tuner,*) plex_next=yes ;;
    # A new .env only: on an existing one, scripts/stack.sh apply asks before stopping what runs now.
    *) if [ "$new_env" = yes ]; then apply_now=yes; fi ;;
esac
# The password is shown once, when kia has just made it; after that, where it is.
[ -s state/admin-password.txt ] && had_password=yes || had_password=no
step "Starting kia (the first build takes a few minutes)"
if [ "$apply_now" = yes ]; then
    # Started once, with the chosen parts, so kia's first refresh isn't cut off by a restart.
    docker compose build
    scripts/stack.sh apply --yes || die "scripts/stack.sh check says what to change; then run scripts/stack.sh apply"
else
    docker compose up -d --build
    if [ -n "$stack" ] && [ "$plex_next" = no ]; then say "KIA_STACK=$stack is set; start it with:  cd $DIR && scripts/stack.sh apply"; fi
fi

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
if [ "$had_password" = yes ]; then
    say "Password for changes: in $DIR/state/admin-password.txt"
elif [ -n "$password" ]; then
    say "Password for changes: $password   (also in $DIR/state/admin-password.txt)"
fi
say "Playlist for players: $url/curated.m3u"
say ""
if [ "$plex_next" = yes ]; then
    say "For Plex (KIA_STACK=$stack is already set):"
    say "  1. When the first refresh is done, make the playlist named plex on the Secure Dashboard,"
    case ",$stack," in
        *,plex,*)
            say "     with For Plex through Threadfin ticked (docs/cookbooks/plex-box.md, step 2)."
            say "  2. Sign in at https://plex.tv/claim, copy the code into PLEX_CLAIM in $DIR/.env, and"
            say "     within 4 minutes run:  cd $DIR && scripts/stack.sh apply   (step 4)" ;;
        *)
            say "     with For Plex through Threadfin ticked (docs/plex.md, step 1)."
            say "  2. Run:  cd $DIR && scripts/stack.sh apply   (docs/plex.md, step 2)" ;;
    esac
    say ""
fi
# What's running now (with Plex, the rest starts with it, after the claim code).
if [ "$apply_now" = yes ]; then
    case ",$stack," in *,jellyfin,*) say "For Jellyfin: open http://$host:8096 and follow docs/cookbooks/jellyfin-box.md, step 3."; say "" ;; esac
    case ",$stack," in *,observability,*) say "Graphs: https://$host:8788 (Grafana; docs/cookbooks/observability.md, step 3)."; say "" ;; esac
fi
say "Next: wait for the first refresh (the Dashboard says when it's done), then set up"
say "Kodi or another player on each device:"
say "  https://github.com/southsidedean/kodi-iptv-assistant/blob/main/docs/kodi.md"
say "  https://github.com/southsidedean/kodi-iptv-assistant/blob/main/docs/players.md (other players)"
