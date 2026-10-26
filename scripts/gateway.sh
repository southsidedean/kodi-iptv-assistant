#!/bin/sh
# The gateway in front of kia's MCP server (the gateway part, docs/mcp.md): a key for each
# device that uses it, a limit for each, and a log of every call.
#
#   scripts/gateway.sh add NAME [--for ASSISTANT]  a key for a new device, shown once
#   scripts/gateway.sh remove NAME                 that device's key stops working
#   scripts/gateway.sh list                        the devices that have a key
#   scripts/gateway.sh write                       write the gateway's settings again
#                                                  (scripts/stack.sh apply does this)
#
# NAME is the device's name in the log, such as laptop or office-pc: lowercase letters,
# digits, and dashes. The gateway reads its settings again within seconds of a change, so
# adding or removing a key needs no restart.
set -eu

cd "$(dirname "$0")/.."
dir=gateway
devices=$dir/devices          # one line per device: its name, and the SHA-256 of its key
config=$dir/config.yaml       # the gateway's settings, written from devices and .env
kia_token=$dir/kia-token      # the gateway's own token for kia (kia.mcp reads it too)

die() { echo "gateway: $*" >&2; exit 1; }
get() { grep "^$1=" .env 2>/dev/null | tail -n 1 | cut -d= -f2- || true; }

cmd=${1:-}
[ $# -gt 0 ] && shift
name="" for_="claude-code"
case "$cmd" in
    add | remove)
        [ $# -gt 0 ] || die "$cmd needs the device's name, such as laptop"
        name=$1; shift ;;
    list | write) ;;
    -h | --help | "") sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown command $cmd (see --help)" ;;
esac
while [ $# -gt 0 ]; do
    case "$1" in
        --for) [ $# -gt 1 ] || die "--for needs an assistant, such as claude-code"; for_=$2; shift ;;
        *) die "unknown option $1 (see --help)" ;;
    esac
    shift
done
# The name goes into the gateway's settings and its log: nothing that could break out of a
# YAML line, and nothing that could pass for an option.
if [ -n "$name" ]; then
    case "$name" in -* | *[!a-z0-9-]*) die "a device's name is lowercase letters, digits, and dashes, such as laptop" ;; esac
    [ ${#name} -le 40 ] || die "a device's name is at most 40 characters"
fi
case "$for_" in claude-code | claude-desktop | gemini | codex | vscode | cursor | curl) ;; *) die "kia doesn't know the assistant $for_" ;; esac

sha256() {  # the SHA-256 of what comes in, in hex
    if command -v sha256sum >/dev/null 2>&1; then sha256sum | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then shasum -a 256 | cut -d' ' -f1
    else openssl dgst -sha256 -r | cut -d' ' -f1
    fi
}
# 32 random bytes, in letters, digits, - and _: safe in a header, a shell, and YAML.
new_key() { head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n'; }

# Only you (and kia and the gateway, which run as you) can read anything in gateway/.
umask 077
mkdir -p "$dir"
[ ! -L "$dir" ] && [ -d "$dir" ] || die "$dir isn't a folder"
touch "$devices"

# The settings, from the devices and .env. Written to a new file and renamed into place, so
# the gateway never reads half of them.
write() {
    rate=$(get KIA_GATEWAY_RATE); rate=${rate:-120}
    case "$rate" in "" | *[!0-9]*) die "KIA_GATEWAY_RATE is a number of requests a minute, such as 120" ;; esac
    [ "$rate" -ge 1 ] && [ "$rate" -le 10000 ] || die "KIA_GATEWAY_RATE is from 1 to 10000 requests a minute"
    # The gateway's own token for kia, made the first time. kia accepts it as it does its own
    # (state/mcp-token.txt), so with the gateway in use, kia's own token can be turned off.
    if [ ! -s "$kia_token" ]; then new_key >"$kia_token.tmp" && mv -f "$kia_token.tmp" "$kia_token"; fi
    if [ -s "$devices" ]; then
        keys=$(awk '{ printf "          - keyHash: sha256:%s\n            metadata:\n              device: %s\n", $2, $1 }' "$devices")
    else
        keys="          []"  # no devices yet: every call is refused
    fi
    cat >"$config.tmp" <<EOF
# kia's gateway (agentgateway) in front of kia's MCP server. scripts/gateway.sh writes this
# file from gateway/devices and .env: a change made here by hand is lost at its next write.
config:
  # Its admin page could change these settings: off.
  adminAddr: "off"
  logging:
    format: json
# One line a call: the time, who sent it (the device), the tool, the answer's status, and
# how long it took. Never a key or a token. (docker compose logs gateway)
frontendPolicies:
  accessLog:
    add:
      device: apiKey.device
binds:
- port: 8745
  listeners:
  - protocol: HTTPS
    # kia's certificate (tls/), or yours if you put one there: devices trust it as they
    # trust the secure Dashboard.
    tls:
      cert: /etc/kia-tls/fullchain.pem
      key: /etc/kia-tls/privkey.pem
    routes:
    - matches:
      - path:
          exact: /mcp
      policies:
        # A key for each device, kept only as its SHA-256: this file can't give one away.
        apiKey:
          mode: strict
          location:
            header:
              name: Authorization
              prefix: "Bearer "
          keys:
$keys
        # Requests a minute for each device (KIA_GATEWAY_RATE). Every request counts, an
        # assistant's start-up ones too; over it, the device gets "rate limit exceeded".
        localRateLimit:
        - maxTokens: $rate
          tokensPerFill: $rate
          fillInterval: 60s
          type: requests
          key: apiKey.device
      backends:
      - mcp:
          # kia's MCP server keeps no sessions: each request stands alone.
          statefulMode: stateless
          targets:
          - name: kia
            mcp:
              host: https://web:443/api/mcp
            policies:
              # The gateway's own token for kia. Devices never see it.
              backendAuth:
                key:
                  value:
                    file: /gateway/kia-token
                  location:
                    header:
                      name: Authorization
                      prefix: "Bearer "
              # Only kia's own certificate is trusted. The name isn't checked, because kia's
              # certificate names the computer, not "web", its name on the gateway's network,
              # which only the gateway, kia, and its web server are on.
              backendTLS:
                root: /etc/kia-tls/fullchain.pem
                insecureHost: true
EOF
    mv -f "$config.tmp" "$config"
}

case "$cmd" in
    list)
        if [ -s "$devices" ]; then cut -d' ' -f1 "$devices"; else echo "No devices have a key yet: scripts/gateway.sh add laptop"; fi
        ;;
    write)
        write
        echo "Wrote $config ($(grep -c . "$devices" || true) device(s))."
        ;;
    remove)
        grep -q "^$name " "$devices" || die "no device is called $name (scripts/gateway.sh list)"
        grep -v "^$name " "$devices" >"$devices.tmp" || true
        mv -f "$devices.tmp" "$devices"
        write
        echo "Removed $name: its key no longer works."
        ;;
    add)
        if grep -q "^$name " "$devices"; then die "$name already has a key: remove it first to make a new one"; fi
        key=$(new_key)
        [ ${#key} -ge 40 ] || die "couldn't make a key (no /dev/urandom?)"
        printf '%s %s\n' "$name" "$(printf '%s' "$key" | sha256)" >>"$devices"
        write
        echo "Added $name. Its key is shown only now; keep it with the device's assistant:"
        echo
        echo "  $key"
        echo
        # The setup for its assistant, with the gateway's address, from kia (kia mcp-token);
        # the key goes in on stdin, never on a command line.
        if ! printf '%s\n' "$key" | docker compose exec -T kia python3 -m kia mcp-token --gateway --for "$for_" 2>/dev/null; then
            port=$(get KIA_GATEWAY_PORT)
            echo "The gateway's address is https://<kia's address>:${port:-8745}/mcp, with this key as the"
            echo "Bearer token in place of kia's own (docs/mcp.md)."
        fi
        ;;
esac
