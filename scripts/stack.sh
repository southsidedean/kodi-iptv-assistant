#!/bin/sh
# Choose which parts of kia's stack run, check the choice, and start it (docs/stack.md).
# The choice is KIA_STACK in .env, such as KIA_STACK=plex,observability.
#
#   scripts/stack.sh                    what's chosen, what runs, and whether it has data
#   scripts/stack.sh check              check KIA_STACK; change nothing
#   scripts/stack.sh apply              check, then start what's chosen and stop the rest
#   scripts/stack.sh apply --preset P   first set KIA_STACK from stacks/P.env
#   scripts/stack.sh check --stack S    check S as if it were KIA_STACK; change nothing
#   scripts/stack.sh apply --stack S    first set KIA_STACK to S
#   scripts/stack.sh list               the parts you can choose, and the presets
#   scripts/stack.sh watch              run for the Dashboard's Stack page (the kia-stack service)
#   --yes                               don't ask, even if someone is watching
set -eu

cd "$(dirname "$0")/.."
cmd=status preset="" try="" try_set=no ask=yes once=no
while [ $# -gt 0 ]; do
    case "$1" in
        status | check | apply | list | watch) cmd=$1 ;;
        --preset) [ $# -gt 1 ] || { echo "stack: --preset needs a name (scripts/stack.sh list)" >&2; exit 2; }; preset=$2; shift ;;
        --stack) [ $# -gt 1 ] || { echo "stack: --stack needs the parts, such as plex,observability" >&2; exit 2; }; try=$2; try_set=yes; shift ;;
        --once) once=yes ;;  # watch: one round, then stop (for tests)
        --yes | -y) ask=no ;;
        -h | --help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "stack: unknown option $1 (see --help)" >&2; exit 2 ;;
    esac
    shift
done

OS=$(uname -s)
# Each part you can choose: its name, the Compose profiles it turns on, and what it is.
PARTS="plex|plex plex-server|Plex in the stack, with Threadfin as its tuner (docs/cookbooks/plex-box.md)
plex-tuner|plex|Threadfin as the tuner for a Plex on another computer (docs/plex.md)
plex-tuner-2|plex-2|a second Threadfin, for more than 480 channels in Plex
jellyfin|jellyfin-server|Jellyfin in the stack (docs/cookbooks/jellyfin-box.md)
observability|monitoring|Prometheus and Grafana, and node-exporter on Linux (docs/cookbooks/observability.md)
observability-gpu|monitoring-gpu|the graphics card on the Hardware dashboard: NVIDIA, Linux
observability-disks|monitoring-disks|each disk's health (SMART) on the Hardware dashboard: Linux"

get() { grep "^$1=" .env 2>/dev/null | tail -n 1 | cut -d= -f2- || true; }
set_env() {  # set_env NAME VALUE: replace the line, or add it
    if grep -q "^$1=" .env; then
        (umask 077 && awk -v k="$1" -v v="$2" 'BEGIN { FS = OFS = "=" } $1 == k { print k "=" v; next } { print }' .env >.env.tmp) && mv .env.tmp .env
    else
        printf '%s=%s\n' "$1" "$2" >>.env
    fi
}
has() { case ",$features," in *",$1,"*) return 0 ;; esac; return 1; }
listening() {  # something answers on this port here
    if command -v nc >/dev/null 2>&1; then nc -z -w 2 127.0.0.1 "$1" >/dev/null 2>&1
    elif command -v ss >/dev/null 2>&1; then ss -Hltn "sport = :$1" 2>/dev/null | grep -q .
    else return 1
    fi
}
running() { docker compose ps --services --status running 2>/dev/null | grep -qx "$1"; }

if [ "$cmd" = list ]; then
    echo "Parts you can choose, for KIA_STACK in .env (comma between them):"
    echo "$PARTS" | while IFS='|' read -r name _ what; do printf '  %-20s %s\n' "$name" "$what"; done
    echo "Presets (scripts/stack.sh apply --preset NAME):"
    for f in stacks/*.env; do
        printf '  %-20s %s\n' "$(basename "$f" .env)" "$(sed -n '1s/^# *//p' "$f")"
    done
    exit 0
fi

[ -f .env ] || { echo "stack: no .env here; install kia first (docs/install.md)" >&2; exit 1; }

# --- watch ---------------------------------------------------------------------------
# The Dashboard's Stack page can't run Docker: the kia container has no Docker socket, on
# purpose. Instead, kia leaves a request in state/stack-request, and this loop, running on
# the computer as you (the kia-stack service: scripts/install-service.sh --stack), carries
# it out with check or apply and leaves the answer in state/stack-result. Every 30 seconds
# it also writes what each container is doing to state/stack-containers.tsv, for the
# Dashboard to show.
#
# The kia container can write to state/, so the request is untrusted. It's read as plain
# key=value lines and never run, and each value has to match a strict pattern. So the most
# a request can do is what you can do with this script yourself: check or apply a choice of
# the parts it knows.
if [ "$cmd" = watch ]; then
    if [ -n "$preset" ] || [ "$try_set" = yes ]; then echo "stack: watch takes no --preset or --stack" >&2; exit 2; fi
    mkdir -p state
    # One watcher at a time; a second one would carry out the same request again.
    pidfile=state/stack-watch.pid
    if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        echo "stack: another watch is already running (process $(cat "$pidfile"))" >&2
        exit 1
    fi
    echo $$ >"$pidfile"
    trap 'rm -f "$pidfile"' EXIT
    trap 'exit 0' INT TERM

    field() { grep "^$1=" "$2" 2>/dev/null | head -n 1 | cut -d= -f2- || true; }
    # The answer for kia: a few key=value lines, a blank line, then what the script said.
    answer() {  # answer ID ACTION STATE EXIT [FILE WITH THE OUTPUT]
        {
            printf 'id=%s\naction=%s\nstate=%s\nexit=%s\nfinished=%s\n\n' "$1" "$2" "$3" "$4" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            if [ -n "${5:-}" ]; then tail -n 200 "$5"; fi
        } >state/stack-result.tmp
        mv state/stack-result.tmp state/stack-result
    }
    # Each container, one per line, tab between: service, state, health, and Docker's status text.
    containers() {
        if docker compose ps --all --format '{{.Service}}	{{.State}}	{{.Health}}	{{.Status}}' >state/stack-containers.tmp 2>/dev/null; then
            mv state/stack-containers.tmp state/stack-containers.tsv
        else
            rm -f state/stack-containers.tmp  # Docker isn't answering: keep the last good list
        fi
    }
    take_request() {
        [ -f state/stack-request ] || return 0
        # Taken by renaming it, so kia can leave the next request while this one runs.
        mv state/stack-request state/stack-request.taken || return 0
        req=state/stack-request.taken
        id=$(field id "$req") action=$(field action "$req") parts=$(field stack "$req")
        # Without a proper id there's no one to answer: kia always sends one.
        case "$id" in "" | *[!A-Za-z0-9-]*) echo "stack: ignored a request without a proper id"; rm -f "$req"; return 0 ;; esac
        if [ ${#id} -gt 64 ]; then echo "stack: ignored a request with a long id"; rm -f "$req"; return 0; fi
        why=""
        case "$action" in check | apply) ;; *) why="it asked for something other than check or apply"; action=unknown ;; esac
        case "$parts" in *[!a-z0-9,-]*) why="the parts can only be names such as plex,observability" ;; esac
        if [ ${#parts} -gt 200 ]; then why="the list of parts is too long"; fi
        # A request left more than 10 minutes ago (while this service was stopped, say) may
        # no longer be what you want.
        if [ -n "$(find "$req" -mmin +10 2>/dev/null)" ]; then why="it was more than 10 minutes old"; fi
        if [ -n "$why" ]; then
            echo "stack: refused request $id: $why"
            echo "Refused: $why." >state/stack-watch.out
            answer "$id" "$action" refused 2 state/stack-watch.out
            rm -f "$req" state/stack-watch.out
            return 0
        fi
        echo "stack: $action KIA_STACK=$parts (request $id)"
        answer "$id" "$action" running ""  # so the Dashboard can say it's under way
        if [ "$action" = apply ]; then set -- apply --stack "$parts" --yes; else set -- check --stack "$parts"; fi
        code=0
        sh scripts/stack.sh "$@" >state/stack-watch.out 2>&1 </dev/null || code=$?
        answer "$id" "$action" finished "$code" state/stack-watch.out
        rm -f "$req" state/stack-watch.out
        containers  # apply changes what runs: show it at once
    }

    echo "stack: watching for requests from the Dashboard's Stack page (Ctrl-C stops)"
    round=0
    while :; do
        if [ $((round % 10)) -eq 0 ]; then containers; fi  # every 30 seconds
        take_request
        [ "$once" = no ] || exit 0
        round=$((round + 1))
        sleep 3
    done
fi

if [ -n "$preset" ]; then
    [ "$cmd" = apply ] || { echo "stack: --preset goes with apply" >&2; exit 2; }
    [ -f "stacks/$preset.env" ] || { echo "stack: there's no preset $preset (scripts/stack.sh list)" >&2; exit 2; }
    chosen=$(grep '^KIA_STACK=' "stacks/$preset.env" | cut -d= -f2-)
    set_env KIA_STACK "$chosen"
    echo "KIA_STACK=$chosen (preset $preset)"
fi

if [ "$try_set" = yes ]; then
    # Only part names, with commas or spaces between: the Dashboard sends this through
    # watch, and set_env hands it to awk, which would read a backslash as an escape.
    case "$try" in *[!a-z0-9,\ -]*) echo "stack: --stack takes part names, such as plex,observability (scripts/stack.sh list)" >&2; exit 2 ;; esac
    [ "$cmd" = check ] || [ "$cmd" = apply ] || { echo "stack: --stack goes with check or apply" >&2; exit 2; }
    [ -z "$preset" ] || { echo "stack: use --preset or --stack, not both" >&2; exit 2; }
    if [ "$cmd" = apply ]; then set_env KIA_STACK "$try"; fi  # the summary below shows it
fi

# check --stack tries a choice without changing .env; otherwise the choice is .env's.
if [ "$try_set" = yes ]; then features=$try; else features=$(get KIA_STACK); fi
features=$(echo "$features" | tr ' ' ',' | tr -s ',' | sed 's/^,//; s/,$//')
chosen=yes
# Not chosen this way yet: no KIA_STACK line (an .env from before it), or an empty one next
# to COMPOSE_PROFILES set by hand. Then COMPOSE_PROFILES decides, and apply leaves it alone.
if [ -z "$features" ] && [ -z "$preset" ] && [ "$try_set" = no ] && { ! grep -q '^KIA_STACK=' .env || [ -n "$(get COMPOSE_PROFILES)" ]; }; then
    chosen=no
    profiles=$(get COMPOSE_PROFILES)
    if grep -q '^KIA_STACK=' .env; then why="KIA_STACK is empty"; else why="KIA_STACK isn't in .env"; fi
    echo "$why, so COMPOSE_PROFILES decides what runs: ${profiles:-kia and its web server only}."
    echo "To choose with this script instead, set KIA_STACK (scripts/stack.sh list shows the parts)."
    [ "$cmd" = status ] || exit 1
fi

# --- check ---------------------------------------------------------------------------
problems=0 warnings=0
problem() { echo "problem: $*"; problems=$((problems + 1)); }
warn() { echo "warning: $*"; warnings=$((warnings + 1)); }
note() { echo "  $*"; }

profiles=""
add() { case " $profiles " in *" $1 "*) ;; *) profiles="${profiles:+$profiles }$1" ;; esac; }
old_ifs=$IFS
IFS=,
for part in $features; do
    IFS=$old_ifs
    line=$(echo "$PARTS" | grep "^$part|" || true)
    if [ -z "$line" ]; then
        problem "KIA_STACK has $part, which isn't a part kia knows (scripts/stack.sh list)"
    else
        for p in $(echo "$line" | cut -d'|' -f2); do add "$p"; done
    fi
    IFS=,
done
IFS=$old_ifs

if has plex && has plex-tuner; then problem "plex already brings its tuner; leave plex-tuner out"; fi
if has plex-tuner-2 && ! has plex && ! has plex-tuner; then problem "plex-tuner-2 goes with plex or plex-tuner"; fi

if ! command -v nc >/dev/null 2>&1 && ! command -v ss >/dev/null 2>&1; then
    note "neither nc nor ss is installed, so ports already in use can't be checked"
fi
nvidia=no
if docker info 2>/dev/null | grep -qi nvidia; then nvidia=yes; fi
if [ "$OS" = Darwin ]; then
    if has observability-gpu || has observability-disks; then
        problem "observability-gpu and observability-disks are for Linux; on a Mac, Docker runs in a Linux machine of its own and can't see the Mac's graphics card or disks"
    fi
    if has observability && ! listening 9100; then
        warn "nothing answers on port 9100, so the Mac's own numbers will be missing: brew install node_exporter, then brew services start node_exporter"
    fi
    for v in KIA_PLEX_CONFIG:plex:plex-config KIA_JELLYFIN_CONFIG:jellyfin:jellyfin-config KIA_GRAFANA_DATA:observability:grafana-data; do
        name=${v%%:*} rest=${v#*:}; part=${rest%%:*}; vol=${rest#*:}
        value=$(get "$name")
        if has "$part" && { [ -z "$value" ] || case "$value" in */*) true ;; *) false ;; esac; }; then
            warn "$name keeps its database in a folder; on a Mac, use the Docker volume ($name=$vol): docs/configuration.md#on-a-mac"
        fi
    done
else
    if has observability; then
        if listening 9100 && ! running node-exporter; then
            note "port 9100 already has a node-exporter (not kia's); Prometheus uses it"
        else
            add monitoring-node
        fi
        if [ "$nvidia" = yes ] && ! has observability-gpu; then note "this computer has an NVIDIA card: add observability-gpu to see it"; fi
        if ! has observability-disks; then note "add observability-disks to see each disk's health"; fi
    fi
    if has observability-gpu && [ "$nvidia" = no ]; then
        problem "observability-gpu needs an NVIDIA card and NVIDIA's Container Toolkit, which Docker doesn't have here"
    fi
    if { has observability-gpu || has observability-disks; } && ! has observability; then
        problem "observability-gpu and observability-disks go with observability"
    fi
fi

if has plex; then
    [ -n "$(get KIA_PLEX_ADVERTISE)" ] || warn "KIA_PLEX_ADVERTISE is empty: Plex apps on your network may not find Plex (docs/cookbooks/plex-box.md)"
    if ! running plex && [ -z "$(get PLEX_CLAIM)" ]; then
        warn "if this is Plex's first start, set PLEX_CLAIM from plex.tv/claim first, or Plex won't be on your account"
    fi
fi
# Threadfin reads kia's playlist for Plex, and stops with an error while it doesn't exist.
if has plex || has plex-tuner; then
    if has plex; then guide="docs/cookbooks/plex-box.md, step 2"; else guide="docs/plex.md, step 1"; fi
    first=$(get KIA_THREADFIN_PLAYLIST)
    if [ -n "$first" ] && [ ! -f "output/$first.m3u" ]; then
        problem "there's no playlist named $first (KIA_THREADFIN_PLAYLIST) for Threadfin yet: make it on the secure Dashboard, under Playlists"
    elif [ -z "$first" ] && [ ! -f output/plex.m3u ] && [ ! -f output/plex-1.m3u ]; then
        problem "there's no playlist named plex for Threadfin yet: make it on the secure Dashboard, under Playlists, with For Plex through Threadfin ticked ($guide)"
    fi
    if has plex-tuner-2; then
        second=$(get KIA_THREADFIN_2_PLAYLIST); second=${second:-plex-2}
        [ -f "output/$second.m3u" ] || problem "there's no playlist named $second for the second Threadfin yet: make it on the secure Dashboard"
    fi
fi
if has plex-tuner && [ -z "$(get KIA_PLEX_URL)" ]; then
    warn "KIA_PLEX_URL is empty, so kia can't add its tuners to Plex for you (docs/plex.md)"
fi
if has jellyfin && [ -z "$(get KIA_JELLYFIN_ADVERTISE)" ]; then
    warn "KIA_JELLYFIN_ADVERTISE is empty: Jellyfin apps on your network may not find Jellyfin"
fi
if has plex || has jellyfin; then
    media=$(get KIA_MEDIA_DIR); media=${media:-./media}
    if [ ! -d "$media" ]; then problem "KIA_MEDIA_DIR ($media) isn't a folder"
    elif [ ! -r "$media" ]; then problem "KIA_MEDIA_DIR ($media) can't be read"
    fi
fi
[ -n "$(get KIA_PUBLIC_URL)" ] || warn "KIA_PUBLIC_URL is empty: players get links without kia's address (docs/configuration.md)"

# Ports for parts that aren't running yet.
for v in plex:plex:KIA_PLEX_PORT:32400 jellyfin:jellyfin:KIA_JELLYFIN_PORT:8096 observability:grafana:KIA_GRAFANA_PORT:8788 observability:prometheus:KIA_PROMETHEUS_PORT:8789; do
    part=${v%%:*} rest=${v#*:}; service=${rest%%:*}; rest=${rest#*:}; setting=${rest%%:*}; default=${rest#*:}
    port=$(get "$setting"); port=${port:-$default}
    if has "$part" && ! running "$service" && listening "$port"; then
        problem "port $port, for $service, is in use; set $setting to another one"
    fi
done

# A small computer running both media servers and the graphs.
if [ "$OS" = Darwin ]; then mem_kb=$(($(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1024)); else mem_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0); fi
heavy=0
for part in plex jellyfin observability; do if has "$part"; then heavy=$((heavy + 1)); fi; done
if [ "${mem_kb:-0}" -gt 0 ] && [ "$mem_kb" -lt 6000000 ] && [ "$heavy" -ge 2 ]; then
    warn "this computer has $((mem_kb / 1024 / 1024)) GB of memory; with $heavy of plex, jellyfin, and observability, it may run short (docs/setups.md)"
fi

if [ "$chosen" = yes ]; then
    echo "KIA_STACK=$features"
    echo "COMPOSE_PROFILES=$(echo "$profiles" | tr ' ' ',')"
fi
if [ "$cmd" = check ] || [ "$cmd" = apply ]; then
    echo "$problems problem(s), $warnings warning(s)."
    [ "$problems" -eq 0 ] || exit 1
fi

# --- status --------------------------------------------------------------------------
if [ "$cmd" = status ]; then
    echo
    docker compose ps --format '{{.Service}}: {{.Status}}' 2>/dev/null || echo "Docker isn't answering."
    if running kia && running prometheus; then
        echo
        echo "Dashboards (which panels have data here):"
        docker compose exec -T kia python3 -m kia check-dashboards </dev/null || true
    fi
    exit 0
fi

[ "$cmd" = apply ] || exit 0

# --- apply ---------------------------------------------------------------------------
# Nobody watching? Restarting kia cuts off Pluto TV through it, and Plex's Live TV.
port=$(get KIA_PORT)
watching=$(curl -fsS --max-time 5 "http://localhost:${port:-8780}/metrics" 2>/dev/null | awk '
    /^kia_pluto_channels_playing / && $2 > 0 { n += $2 }
    /^kia_(plex|jellyfin)_sessions\{/ && $0 !~ /kind="none"/ { n += $NF }
    /^kia_refresh_running 1/ { r = 1 }
    END { if (n > 0) print n " playing"; else if (r) print "a refresh is running" }' || true)
if [ -n "$watching" ] && [ "$ask" = yes ]; then
    printf 'kia says: %s. Change the stack now anyway? [y/N] ' "$watching"
    { read -r answer </dev/tty; } 2>/dev/null || answer=n
    case "$answer" in [yY]*) ;; *) echo "Not changed. Run this again later, or with --yes."; exit 0 ;; esac
fi

# Parts that run now and aren't chosen any more: say so first (moving from COMPOSE_PROFILES
# to KIA_STACK, a part left out would stop).
dropped=""
for p in $(get COMPOSE_PROFILES | tr ',' ' '); do
    case " $profiles " in *" $p "*) ;; *) dropped="${dropped:+$dropped, }$p" ;; esac
done
if [ -n "$dropped" ] && [ "$ask" = yes ]; then
    printf 'These Compose profiles stop, as KIA_STACK leaves them out: %s. Go ahead? [y/N] ' "$dropped"
    { read -r answer </dev/tty; } 2>/dev/null || answer=n
    case "$answer" in [yY]*) ;; *) echo "Not changed. Add what you want to keep to KIA_STACK (scripts/stack.sh list)."; exit 0 ;; esac
fi

mkdir -p state
cp -p .env "state/env.before-stack-$(date +%Y%m%d-%H%M%S)"
set_env COMPOSE_PROFILES "$(echo "$profiles" | tr ' ' ',')"
if has plex && [ -z "$(get KIA_PLEX_URL)" ]; then set_env KIA_PLEX_URL http://plex:32400; fi
if has jellyfin && [ -z "$(get KIA_JELLYFIN_URL)" ]; then set_env KIA_JELLYFIN_URL http://jellyfin:8096; fi

# The profiles given here too: one exported in this shell would win over .env's.
compose_profiles=$(echo "$profiles" | tr ' ' ',')
COMPOSE_PROFILES=$compose_profiles docker compose up -d --quiet-pull
# Parts no longer chosen keep running until they're stopped.
wanted=$(COMPOSE_PROFILES=$compose_profiles docker compose config --services)
# Any state: one stuck restarting, or a finished setup container, goes too.
for service in $(COMPOSE_PROFILES='*' docker compose ps --all --services 2>/dev/null); do
    if ! echo "$wanted" | grep -qx "$service"; then
        COMPOSE_PROFILES='*' docker compose rm --stop --force "$service" && echo "Stopped $service: it isn't in KIA_STACK any more."
    fi
done
echo "Done. Your old .env is in state/env.before-stack-*. scripts/stack.sh shows what runs."
if has plex; then echo "Next for Plex: docker compose exec kia python3 -m kia plex-tuners (docs/cookbooks/plex-box.md)"; fi
if has jellyfin; then echo "Next for Jellyfin: open it on port $(get KIA_JELLYFIN_PORT | grep . || echo 8096) and follow docs/cookbooks/jellyfin-box.md, step 3"; fi
