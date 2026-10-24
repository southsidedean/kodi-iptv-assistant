#!/bin/sh
# Run kia without Docker, as a service that starts with the computer: a systemd user
# service on Linux, a launchd agent on macOS. Run it in the kia folder:
#
#   scripts/install-service.sh              install (or update) and start
#   scripts/install-service.sh --uninstall  stop and remove the service
#   scripts/install-service.sh --dry-run    show the service file without installing
#
# With --stack (and the same options), it's the kia-stack service instead, for kia with
# Docker: scripts/stack.sh watch, which carries out the changes you make on the Dashboard's
# Stack page (docs/stack.md). It needs only Docker and the kia folder's .env.
#
# It needs Python 3.11+ (nothing else from Python), ffmpeg, git, and openssl, and Node.js 22+ for guide downloads
# from websites. Settings come from .env (copy .env.example) and kia.toml, the same as
# with Docker. kia serves the playlists, the Dashboard, and the secure page itself.
set -eu

DIR=$(cd "$(dirname "$0")/.." && pwd)
MODE=install STACK=no
for arg in "$@"; do
    case "$arg" in
        --uninstall) MODE=uninstall ;;
        --dry-run) MODE=dry-run ;;
        --stack) STACK=yes ;;
        *) printf 'Usage: %s [--stack] [--uninstall | --dry-run]\n' "$0" >&2; exit 2 ;;
    esac
done

say() { printf '%s\n' "$*"; }
die() { printf 'Stopped: %s\n' "$*" >&2; exit 1; }
OS=$(uname -s)
# kia itself, or the kia-stack service: their names, and the templates in deploy/.
if [ "$STACK" = yes ]; then NAME=kia-stack; else NAME=kia; fi
LABEL=io.github.southsidedean.$NAME
UNIT=$HOME/.config/systemd/user/$NAME.service
PLIST=$HOME/Library/LaunchAgents/$LABEL.plist

xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

# .env's settings as launchd EnvironmentVariables entries (systemd reads .env directly).
plist_env() {
    [ -f "$DIR/.env" ] || return 0
    grep -E '^[A-Z_][A-Z0-9_]*=.+' "$DIR/.env" | while IFS= read -r line; do
        key=${line%%=*} value=${line#*=}
        # --dry-run shows the file on screen: passwords and tokens stay out of it.
        if [ "$MODE" = dry-run ]; then
            case "$key" in *PASSWORD* | *TOKEN* | *SECRET* | *NTFY_URL* | *CLAIM*) value="(from .env, not shown)" ;; esac
        fi
        printf '    <key>%s</key>\n    <string>%s</string>\n' "$key" "$(xml "$value")"
    done
}

render() {  # template -> service file text (values via the environment, so awk leaves them as they are)
    KIA_R_DIR=$DIR KIA_R_PATH=$PATH KIA_R_PYTHON=$(command -v python3 || echo python3) KIA_R_ENV=$(plist_env) awk '
        BEGIN { dir = ENVIRON["KIA_R_DIR"]; path = ENVIRON["KIA_R_PATH"]; python = ENVIRON["KIA_R_PYTHON"]; env = ENVIRON["KIA_R_ENV"] }
        /^@ENV@$/ { if (env != "") print env; next }
        { while ((i = index($0, "@DIR@")) > 0) $0 = substr($0, 1, i - 1) dir substr($0, i + 5)
          while ((i = index($0, "@PATH@")) > 0) $0 = substr($0, 1, i - 1) path substr($0, i + 6)
          while ((i = index($0, "@PYTHON@")) > 0) $0 = substr($0, 1, i - 1) python substr($0, i + 8)
          print }' "$1"
}

if [ "$MODE" = uninstall ]; then
    if [ "$OS" = Darwin ]; then
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        rm -f "$PLIST"
    else
        systemctl --user disable --now "$NAME" 2>/dev/null || true
        rm -f "$UNIT"
        systemctl --user daemon-reload 2>/dev/null || true
    fi
    say "Removed the $NAME service. Your settings and data in $DIR are untouched."
    exit 0
fi

case "$OS" in
    Darwin) template=$DIR/deploy/$NAME.plist; target=$PLIST ;;
    Linux) template=$DIR/deploy/$NAME.service; target=$UNIT ;;
    *) die "this script is for macOS and Linux" ;;
esac

if [ "$MODE" = dry-run ]; then
    render "$template"
    exit 0
fi

# The kia-stack service: Docker and kia's .env are all it needs, and it holds no settings.
if [ "$STACK" = yes ]; then
    command -v docker >/dev/null 2>&1 || die "Docker isn't installed. The kia-stack service is for kia with Docker."
    [ -f "$DIR/.env" ] || die "there's no .env in $DIR. Install kia first (docs/install.md)."
    mkdir -p "$DIR/state" "$DIR/work/logs" "$(dirname "$target")"
    render "$template" >"$target.tmp"
    mv "$target.tmp" "$target"
    if [ "$OS" = Darwin ]; then
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        tries=0
        until launchctl bootstrap "gui/$(id -u)" "$target" 2>/dev/null; do
            tries=$((tries + 1))
            [ $tries -lt 10 ] || die "launchd didn't start kia-stack. Try: launchctl bootstrap gui/$(id -u) $target"
            sleep 1
        done
        say "Installed $target and started it. It starts again when you log in. Log: $DIR/work/logs/kia-stack.log"
    else
        systemctl --user daemon-reload
        systemctl --user enable kia-stack >/dev/null 2>&1
        systemctl --user restart kia-stack
        say "Installed $target and started it. Log: journalctl --user -u kia-stack -f"
        if [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" != yes ]; then
            say "Right now it runs only while you're logged in. To keep it running, and start it at boot, run once:"
            say "  sudo loginctl enable-linger $(id -un)"
        fi
    fi
    say "The Dashboard's Stack page can now change what runs next to kia."
    exit 0
fi

say "Checking what's installed..."
missing=""
python=$(command -v python3 || true)
if [ -z "$python" ] || ! "$python" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    missing="$missing python3 (3.11 or newer)"
fi
for tool in ffprobe git openssl; do
    command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
done
if [ -n "$missing" ]; then
    if [ "$OS" = Darwin ]; then
        die "missing:$missing. With Homebrew: brew install python ffmpeg git openssl node"
    fi
    die "missing:$missing. On Ubuntu or Debian: sudo apt install python3 ffmpeg git openssl"
fi
if ! command -v node >/dev/null 2>&1 || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 22 ]; then
    say "Note: Node.js 22 or newer isn't installed, so kia can't download guides from websites."
    say "      It still builds the guide from the sources it reads itself. Install Node.js to get the rest."
fi

cd "$DIR"
# kia needs nothing beyond Python itself; the service runs it from src/, so `git pull`
# updates it (restart the service afterwards).
PYTHONPATH=$DIR/src "$python" -m kia --version >/dev/null || die "kia doesn't start with $python"
if ! [ -f .env ]; then
    if [ "$OS" = Darwin ]; then
        address=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo localhost)
        zone=$(readlink /etc/localtime 2>/dev/null | sed 's|.*zoneinfo/||')
    else
        address=$(hostname -I 2>/dev/null | awk '{print $1}')
        zone=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || true)
    fi
    # .env holds passwords, so only you can read it.
    (umask 077 && sed -e "s|^KIA_PUBLIC_URL=.*|KIA_PUBLIC_URL=http://${address:-localhost}:8780|" -e "s|^TZ=.*|TZ=${zone:-Etc/UTC}|" .env.example >.env)
    say "Made .env with address http://${address:-localhost}:8780 and time zone ${zone:-Etc/UTC}. Edit it to change them, then run this again."
fi
mkdir -p output state work/logs vendor profiles tls "$(dirname "$target")"
# The password is shown once, when kia has just made it; after that, where it is.
[ -s state/admin-password.txt ] && had_password=yes || had_password=no

# The service file can hold your .env settings (passwords included), so only you can read it.
(umask 077 && render "$template" >"$target.tmp")
mv "$target.tmp" "$target"

if [ "$OS" = Darwin ]; then
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    # launchd can refuse to start it again while the old one is still stopping; wait and retry.
    tries=0
    until launchctl bootstrap "gui/$(id -u)" "$target" 2>/dev/null; do
        tries=$((tries + 1))
        [ $tries -lt 10 ] || die "launchd didn't start kia. Try: launchctl bootstrap gui/$(id -u) $target"
        sleep 1
    done
    say "Installed $target and started kia. It starts again when you log in."
    say "Log: $DIR/work/logs/kia.log"
else
    systemctl --user daemon-reload
    systemctl --user enable kia >/dev/null 2>&1
    systemctl --user restart kia  # starts it, or picks up a changed service file
    say "Installed $target and started kia."
    if [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" = yes ]; then
        say "It starts again with the computer."
    else
        say "Right now it runs only while you're logged in. To keep it running, and start it at boot, run once:"
        say "  sudo loginctl enable-linger $(id -un)"
    fi
    say "Log: journalctl --user -u kia -f"
fi

url=$(sed -n 's/^KIA_PUBLIC_URL=//p' .env)
say "Dashboard: ${url:-http://localhost:8780}/"
i=0
while [ $i -lt 15 ] && ! [ -s state/admin-password.txt ] && ! grep -q '^KIA_ADMIN_PASSWORD=.' .env; do
    sleep 1
    i=$((i + 1))
done
if [ "$had_password" = yes ]; then
    say "Password for changes on the Dashboard: in $DIR/state/admin-password.txt"
elif [ -s state/admin-password.txt ]; then
    say "Password for changes on the Dashboard: $(cat state/admin-password.txt)"
fi
