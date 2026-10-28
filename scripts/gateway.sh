#!/bin/sh
# The gateway in front of kia's MCP server (the gateway part, docs/mcp.md): a key for each
# device that uses it, a limit for each, and a log of every call.
#
#   scripts/gateway.sh add NAME [--for ASSISTANT]  a key for a new device, shown once
#   scripts/gateway.sh remove NAME                 that device's key stops working
#   scripts/gateway.sh list                        the devices that have a key
#   scripts/gateway.sh write                       write the gateway's settings again
#                                                  (scripts/stack.sh apply does this)
#   scripts/gateway.sh refresh                     restart the gateway if its settings or
#                                                  kia's certificate changed (the kia-stack
#                                                  service does this every 30 seconds)
#
# NAME is the device's name in the log, such as laptop or office-pc: lowercase letters,
# digits, and dashes. A change takes effect at once: the gateway restarts, which takes
# about a second.
set -eu
# Ranges such as [a-z] mean only ASCII letters in the C locale; in others (macOS's sh, in
# UTF-8) they take capitals and accented letters too, and a name check lets them through.
LC_ALL=C
export LC_ALL

cd "$(dirname "$0")/.."
dir=gateway
devices=$dir/devices          # one line per device: its name, and the SHA-256 of its key
config=$dir/config.yaml       # the gateway's settings, written from devices and .env
kia_token=$dir/kia-token      # the gateway's own token for kia (kia.mcp reads it too)
loaded=$dir/loaded            # what the gateway last started with (refresh, below)
cert=tls/fullchain.pem        # kia's certificate, or yours

die() { echo "gateway: $*" >&2; exit 1; }
# A setting's value as Compose reads it: the last line for it, without one pair of quotes around it.
get() { grep "^$1=" .env 2>/dev/null | tail -n 1 | cut -d= -f2- | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/" || true; }

cmd=${1:-}
[ $# -gt 0 ] && shift
name="" for_="claude-code"
case "$cmd" in
    add | remove)
        [ $# -gt 0 ] || die "$cmd needs the device's name, such as laptop"
        name=$1; shift ;;
    list | write | refresh) ;;
    -h | --help | "") sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
if [ -L "$dir" ] || [ ! -d "$dir" ]; then die "$dir isn't a folder"; fi
touch "$devices"

# The settings, from the devices and .env. Written to a new file and renamed into place, so
# the gateway never reads half of them.
write() {
    rate=$(get KIA_GATEWAY_RATE); rate=${rate:-120}
    # 1 to 10000, without a leading 0 (YAML would read 0120 as a number in base 8). stack.sh
    # check uses the same rule, so a value it passes never stops an apply here.
    case "$rate" in "" | *[!0-9]* | 0*) die "KIA_GATEWAY_RATE is a number of requests a minute, such as 120" ;; esac
    if [ ${#rate} -gt 5 ] || [ "$rate" -gt 10000 ]; then die "KIA_GATEWAY_RATE is from 1 to 10000 requests a minute"; fi
    # The gateway's own token for kia, made the first time. kia accepts it as it does its own
    # (state/mcp-token.txt), so with the gateway in use, kia's own token can be turned off.
    if [ ! -s "$kia_token" ]; then new_key >"$kia_token.tmp" && mv -f "$kia_token.tmp" "$kia_token"; fi
    if [ -s "$devices" ]; then
        keys=$(awk 'BEGIN { print "          keys:" } { printf "          - keyHash: sha256:%s\n            metadata:\n              device: %s\n", $2, $1 }' "$devices")
    else
        keys="          keys: []"  # no devices yet: every call is refused
    fi
    # The front door (the frontdoor part): a second listener that owns login and proxies the
    # Dashboard. Its host names and port come from .env; the client and cookie secrets come from
    # the ones scripts/frontdoor.sh wrote. The gateway's OIDC cookie key goes in gateway/oidc.env,
    # which compose loads for the gateway. The file is always written (empty without the front
    # door), so compose's env_file always finds it.
    fd_host=$(get KIA_FRONTDOOR_HOST)
    fd_auth=$(get KIA_FRONTDOOR_AUTH_HOST)
    fd_port=$(get KIA_FRONTDOOR_PORT); fd_port=${fd_port:-8444}
    : >"$dir/oidc.env.tmp"
    if [ -n "$fd_host" ]; then
        [ -n "$fd_auth" ] || die "KIA_FRONTDOOR_AUTH_HOST is needed with KIA_FRONTDOOR_HOST (docs/front-door.md)"
        case "$fd_port" in "" | *[!0-9]* | 0*) die "KIA_FRONTDOOR_PORT is a port number, such as 8444" ;; esac
        if [ ${#fd_port} -gt 5 ] || [ "$fd_port" -gt 65535 ]; then die "KIA_FRONTDOOR_PORT is from 1 to 65535"; fi
        [ -s frontdoor/secrets/client-secret ] || die "set the front door up first: scripts/frontdoor.sh write"
        fd_client=$(cat frontdoor/secrets/client-secret)
        fd_token=$(head -n1 "$kia_token")  # the gateway's token for kia, to prove the request came through the front door
        printf 'OIDC_COOKIE_SECRET=%s\n' "$(cat frontdoor/secrets/cookie-secret)" >"$dir/oidc.env.tmp"
        # The login's public signing key, written as a JWKS file the gateway reads at startup.
        # Derived from the login's own signing key (frontdoor/secrets/oidc.key), so it always
        # matches what the login uses. The gateway can't fetch it at startup instead (the login
        # serves its keys only for its own host, and the gateway's own listener isn't up yet),
        # and the key is kia's and never rotates, so a file is exact and needs no refresh.
        fd_mod=$(openssl rsa -in frontdoor/secrets/oidc.key -noout -modulus 2>/dev/null | sed 's/^Modulus=//')
        fd_n=$(printf '%s' "$fd_mod" | python3 -c 'import sys, base64; print(base64.urlsafe_b64encode(bytes.fromhex(sys.stdin.read().strip())).decode().rstrip("="))') \
            || die "couldn't derive the login's public key (needs openssl and python3)"
        [ -n "$fd_n" ] || die "couldn't read the login's signing key (scripts/frontdoor.sh write)"
        printf '{"keys":[{"kty":"RSA","use":"sig","alg":"RS256","kid":"kia","e":"AQAB","n":"%s"}]}\n' "$fd_n" \
            >"$dir/jwks.json.tmp" && mv -f "$dir/jwks.json.tmp" "$dir/jwks.json"
    fi
    mv -f "$dir/oidc.env.tmp" "$dir/oidc.env"
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
        # The device's name, for kia: it counts each device's tool calls apart (kia.mcp).
        # This replaces any X-Kia-Device a device sends, so a device can't use another's
        # name, and kia trusts the header only with the gateway's token.
        transformations:
          request:
            set:
              x-kia-device: apiKey.device
        # A key for each device, kept only as its SHA-256: this file can't give one away.
        apiKey:
          mode: strict
          location:
            header:
              name: Authorization
              prefix: "Bearer "
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
    if [ -n "$fd_host" ]; then
        # The front door: a login listener for the Dashboard, and the login provider behind it.
        # kia's web server sets the security headers (deploy/nginx.conf), so the gateway doesn't
        # add its own here; it only owns the login and says who you are. The login token is not
        # passed on to the Dashboard. docs/front-door.md.
        cat >>"$config.tmp" <<EOF
- port: $fd_port
  listeners:
  - protocol: HTTPS
    tls:
      cert: /etc/kia-tls/fullchain.pem
      key: /etc/kia-tls/privkey.pem
    routes:
    - hostnames: [$fd_host]
      policies:
        oidc:
          issuer: https://$fd_auth:$fd_port
          authorizationEndpoint: https://$fd_auth:$fd_port/api/oidc/authorization
          # The back-channel token exchange goes through the gateway's own HTTP listener
          # (below), which adds the forwarded headers the login needs for a steady issuer, so
          # the gateway never has to trust the login's self-signed certificate.
          tokenEndpoint: http://127.0.0.1:9092/api/oidc/token
          # The login's public signing key, from the file gateway.sh derived (above). A startup
          # fetch can't work here: the login serves its keys only for its own host, and the
          # gateway's own listener isn't up yet. The key is kia's and static, so the file is exact.
          jwks:
            file: /gateway/jwks.json
          tokenEndpointAuth: clientSecretPost
          clientId: agentgateway
          clientSecret: $fd_client
          redirectURI: https://$fd_host:$fd_port/oidc/callback
          scopes: [profile, email, groups]
          credentials: sessionOrBearer
        # Who logged in, and their groups, for the Dashboard to read. With KIA_TRUST_FRONTDOOR
        # on, kia lets the "admins" group edit without its own password, but only because the
        # gateway's token in x-kia-gateway proves this came through the front door
        # (kia.api.front_door_user). That's a header of the gateway's own, not Authorization,
        # which the login flow uses and strips; the browser never sees it. The X-User-* headers
        # replace any a client sent, so they can't be faked without the token.
        transformations:
          request:
            set:
              x-user-id: jwt.preferred_username
              x-user-email: jwt.email
              x-user-groups: jwt.groups
              x-kia-gateway: '"$fd_token"'
        # kia's web server answers only over HTTPS (changes need it), with its own certificate,
        # which names the computer rather than "web" on the gateway's network.
        backendTLS:
          root: /etc/kia-tls/fullchain.pem
          insecureHost: true
      backends:
      - host: web:443
    # The login itself, proxied to the provider with the forwarded headers it needs for a
    # steady issuer, so it never needs TLS of its own.
    - hostnames: [$fd_auth]
      policies:
        transformations:
          request:
            set:
              x-forwarded-proto: '"https"'
              x-forwarded-host: '"$fd_auth:$fd_port"'
      backends:
      - host: authelia:9091
- port: 9092
  listeners:
  # The gateway's own HTTP listener for the back-channel token exchange, on this container only
  # (not published), adding the same forwarded headers.
  - protocol: HTTP
    routes:
    - policies:
        transformations:
          request:
            set:
              x-forwarded-proto: '"https"'
              x-forwarded-host: '"$fd_auth:$fd_port"'
      backends:
      - host: authelia:9091
EOF
    fi
    mv -f "$config.tmp" "$config"
}

# What the gateway runs with: its settings, and which certificate is in tls/. The
# certificate by its inode and size, from ls, which never opens it: kia's container can
# write tls/, and the kia-stack service runs this, so a link or a pipe put there by a
# taken-over kia is never read. kia replaces its certificate by renaming a new one into
# place (a new inode); one copied over the old file changes in place, which the gateway
# notices by itself.
# shellcheck disable=SC2012  # one name we chose; macOS's find can't print an inode
stamp() { { cat "$config" 2>/dev/null; ls -lnid "$cert" 2>/dev/null | awk '{ print $1, $6 }'; } | sha256; }

# On Linux, the gateway notices a new settings file or certificate by itself. With Docker
# Desktop (macOS, Windows) it notices the first one and misses every one after it, so when
# either changed since the gateway started, it's restarted. That takes about a second, and
# kia keeps no sessions, so a device just sends its next request. A gateway that isn't
# running reads both when it starts.
refresh() {
    now=$(stamp)
    if [ "$now" = "$(cat "$loaded" 2>/dev/null)" ]; then return 0; fi
    if [ -n "$(docker compose ps -q --status running gateway 2>/dev/null)" ]; then
        if ! docker compose restart gateway >/dev/null 2>&1; then
            echo "gateway: couldn't restart the gateway: docker compose restart gateway" >&2
            return 0  # not marked as loaded, so the next refresh tries again
        fi
        echo "Restarted the gateway with its new settings."
    fi
    printf '%s\n' "$now" >"$loaded.tmp"
    mv -f "$loaded.tmp" "$loaded"
}

case "$cmd" in
    list)
        if [ -s "$devices" ]; then cut -d' ' -f1 "$devices"; else echo "No devices have a key yet: scripts/gateway.sh add laptop"; fi
        ;;
    refresh)
        refresh
        ;;
    write)
        write
        refresh
        echo "Wrote $config ($(grep -c . "$devices" || true) device(s))."
        ;;
    remove)
        grep -q "^$name " "$devices" || die "no device is called $name (scripts/gateway.sh list)"
        grep -v "^$name " "$devices" >"$devices.tmp" || true
        mv -f "$devices.tmp" "$devices"
        write
        refresh
        echo "Removed $name: its key no longer works."
        ;;
    add)
        if grep -q "^$name " "$devices"; then die "$name already has a key: remove it first to make a new one"; fi
        key=$(new_key)
        [ ${#key} -ge 40 ] || die "couldn't make a key (no /dev/urandom?)"
        printf '%s %s\n' "$name" "$(printf '%s' "$key" | sha256)" >>"$devices"
        write
        refresh
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
