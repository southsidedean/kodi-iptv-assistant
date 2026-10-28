#!/bin/sh
# The front door's identity provider (the frontdoor part, docs/front-door.md): a small Authelia
# that holds the household's accounts, so one login covers kia's pages. This writes Authelia's
# settings and the one account; the gateway part puts a login in front of the Dashboard and
# trusts what Authelia says (scripts/gateway.sh, from 0.8.2's listener work on).
#
#   scripts/frontdoor.sh write                 write Authelia's settings from .env and the
#                                              secrets (scripts/stack.sh apply does this)
#   scripts/frontdoor.sh account NAME          set the one account's password (asked for it,
#                                              not shown), such as frontdoor.sh account dad
#   scripts/frontdoor.sh list                  the account that can log in
#   scripts/frontdoor.sh refresh               restart Authelia if its settings changed (the
#                                              kia-stack service does this every 30 seconds)
#
# NAME is the login name: lowercase letters, digits, and dashes, such as dad or admin.
# kia never does password hashing itself: it asks Authelia's own tool to do it, so the only
# password crypto in the stack is Authelia's.
set -eu
# Ranges such as [a-z] mean only ASCII letters in the C locale; in others (macOS's sh, in
# UTF-8) they take capitals and accented letters too, and a name check lets them through.
LC_ALL=C
export LC_ALL

cd "$(dirname "$0")/.."
dir=frontdoor
config=$dir/configuration.yml   # Authelia's settings, written from .env and the secrets
users=$dir/users_database.yml   # the accounts, with each password as a hash Authelia made
secrets=$dir/secrets            # the generated secrets, each in its own file, made once
loaded=$dir/loaded              # what Authelia last started with (refresh, below)

die() { echo "frontdoor: $*" >&2; exit 1; }
# A setting's value as Compose reads it: the last line for it, without one pair of quotes around it.
get() { grep "^$1=" .env 2>/dev/null | tail -n 1 | cut -d= -f2- | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/" || true; }
# 32 random bytes, in letters, digits, - and _: safe in a header, a shell, and YAML.
new_key() { head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n'; }

cmd=${1:-}
[ $# -gt 0 ] && shift
name="" email=""
case "$cmd" in
    account)
        [ $# -gt 0 ] || die "account needs the login name, such as dad"
        name=$1; shift ;;
    write | list | refresh) ;;
    -h | --help | "") sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown command $cmd (see --help)" ;;
esac
while [ $# -gt 0 ]; do
    case "$1" in
        --email) [ $# -gt 1 ] || die "--email needs an address"; email=$2; shift ;;
        *) die "unknown option $1 (see --help)" ;;
    esac
    shift
done
# The login name goes into Authelia's settings and becomes a part of a file: nothing that
# could break out of a YAML line or pass for an option.
if [ -n "$name" ]; then
    case "$name" in -* | *[!a-z0-9-]*) die "a login name is lowercase letters, digits, and dashes, such as dad" ;; esac
    [ ${#name} -le 40 ] || die "a login name is at most 40 characters"
fi

# Only you (and the stack's services, which run as you) can read anything in frontdoor/: the
# secrets, the account hashes, and Authelia's database.
umask 077
mkdir -p "$secrets"
if [ -L "$dir" ] || [ ! -d "$dir" ]; then die "$dir isn't a folder"; fi

# The two host names and the port the front door answers on. Both names must sit under one
# parent domain, so a single login cookie covers them: kia.my-server.lan for the pages and
# auth.my-server.lan for the login, both on my-server.lan.
hosts() {
    app=$(get KIA_FRONTDOOR_HOST)
    authhost=$(get KIA_FRONTDOOR_AUTH_HOST)
    port=$(get KIA_FRONTDOOR_PORT); port=${port:-8444}
    [ -n "$app" ] || die "set KIA_FRONTDOOR_HOST in .env, the name for kia's pages, such as kia.my-server.lan"
    [ -n "$authhost" ] || die "set KIA_FRONTDOOR_AUTH_HOST in .env, the name for the login, such as auth.my-server.lan"
    # A host name: labels of lowercase letters, digits, and dashes, with at least one dot, so
    # it has a parent domain. fullmatch, so a trailing newline or stray character can't pass.
    for h in "$app" "$authhost"; do
        case "$h" in
            *[!a-z0-9.-]* | .* | *. | *..* | -* | *-.* | *.-* | *-) die "$h isn't a host name (lowercase letters, digits, dashes, and dots)" ;;
            *.*) ;;
            *) die "$h needs a parent domain, such as kia.my-server.lan, not just kia" ;;
        esac
    done
    # The parent domain is the name without its first label. Both names must share it.
    domain=${app#*.}
    [ "$authhost" = "$domain" ] || [ "${authhost#*.}" = "$domain" ] || die "KIA_FRONTDOOR_HOST and KIA_FRONTDOOR_AUTH_HOST must share a parent domain (such as my-server.lan)"
    case "$port" in "" | *[!0-9]* | 0*) die "KIA_FRONTDOOR_PORT is a port number, such as 8444" ;; esac
    if [ ${#port} -gt 5 ] || [ "$port" -gt 65535 ] || [ "$port" -lt 1 ]; then die "KIA_FRONTDOOR_PORT is from 1 to 65535"; fi
}

# A secret made once and kept, so writing the settings again never changes it: rotating the
# login cookie key or the signing key would log everyone out, and rotating the client secret
# would stop the gateway. Each is its own file in frontdoor/secrets.
secret() {
    f=$secrets/$1
    if [ ! -s "$f" ]; then new_key >"$f.tmp" && mv -f "$f.tmp" "$f"; fi
    cat "$f"
}

# Authelia's own tool turns a password into a hash: kia never hashes a password itself. It runs
# in a throwaway Authelia container (the pinned image). Authelia takes the password only on its
# command line or from a terminal (it has no stdin form), so it goes on the throwaway container's
# command line, seen only on this host for the instant it runs, and never kept. Prints the hash,
# or stops with a clear message if Docker can't run it.
hash_password() {  # hash_password ALGO PASSWORD
    out=$(docker compose --profile frontdoor run --rm --no-deps authelia \
              authelia crypto hash generate "$1" --password "$2" --no-confirm 2>/dev/null) \
        || die "couldn't run Authelia to hash the password (is Docker running? is the image pulled?)"
    printf '%s\n' "$out" | sed -n 's/^Digest: //p' | head -n 1
}

# Authelia's settings, from .env and the secrets. Written to a new file and renamed into place,
# so Authelia never reads half of them. The OIDC client secret is stored as a hash Authelia
# made (plaintext client secrets are deprecated); the gateway keeps the matching plaintext in
# frontdoor/secrets/client-secret and sends that.
write() {
    hosts
    oidc_key=$secrets/oidc.key
    if [ ! -s "$oidc_key" ]; then
        openssl genrsa -out "$oidc_key.tmp" 2048 2>/dev/null || die "couldn't make the signing key with openssl"
        mv -f "$oidc_key.tmp" "$oidc_key"
    fi
    client=$(secret client-secret)      # the gateway sends this
    # The gateway's OIDC session cookie key (read by gateway.sh). agentgateway wants an
    # AES-256 key as 64 hex characters, not the base64 the others use. Made once and kept.
    if [ ! -s "$secrets/cookie-secret" ]; then
        openssl rand -hex 32 >"$secrets/cookie-secret.tmp" && mv -f "$secrets/cookie-secret.tmp" "$secrets/cookie-secret"
    fi
    session=$(secret session-secret)
    storage=$(secret storage-key)
    hmac=$(secret hmac-secret)
    jwt=$(secret jwt-secret)
    # Hash the client secret once and keep the hash, so a later write doesn't churn it (a new
    # hash would restart Authelia for no reason). The hash proves the same plaintext either way.
    if [ ! -s "$secrets/client-secret.hash" ]; then
        h=$(hash_password pbkdf2 "$client")
        case "$h" in \$pbkdf2*) ;; *) die "Authelia didn't return a hash for the client secret" ;; esac
        printf '%s\n' "$h" >"$secrets/client-secret.hash.tmp" && mv -f "$secrets/client-secret.hash.tmp" "$secrets/client-secret.hash"
    fi
    client_hash=$(cat "$secrets/client-secret.hash")
    # Authelia won't start without an accounts file; account (below) fills it. Make an empty
    # one so a first write and start don't fail before anyone's account is set.
    [ -e "$users" ] || printf 'users: {}\n' >"$users"
    cat >"$config.tmp" <<EOF
# kia's front door (Authelia). scripts/frontdoor.sh writes this file from .env and the secrets
# in frontdoor/secrets: a change made here by hand is lost at its next write.
theme: 'light'
server:
  address: 'tcp://0.0.0.0:9091'
log:
  level: 'info'
# The login reset flow isn't offered (no mail is set up); the secret is still required.
identity_validation:
  reset_password:
    jwt_secret: '$jwt'
authentication_backend:
  file:
    path: '/config/users_database.yml'
# One login is enough (one_factor). Roles come later from groups; for now every account can see
# everything behind the front door.
access_control:
  default_policy: 'one_factor'
session:
  secret: '$session'
  cookies:
    - domain: '$domain'
      authelia_url: 'https://$authhost:$port'
      default_redirection_url: 'https://$app:$port'
storage:
  encryption_key: '$storage'
  local:
    path: '/config/db.sqlite3'
notifier:
  filesystem:
    filename: '/config/notification.txt'
identity_providers:
  oidc:
    hmac_secret: '$hmac'
    jwks:
      - key_id: 'kia'
        key: |
$(sed 's/^/          /' "$oidc_key")
    claims_policies:
      # Put the groups, email, and name in the ID token, so the gateway can read them without a
      # second call. (By default Authelia keeps them for the userinfo endpoint only.)
      kia:
        id_token: ['groups', 'email', 'preferred_username', 'name']
    clients:
      - client_id: 'agentgateway'
        client_name: 'kia front door'
        client_secret: '$client_hash'
        public: false
        authorization_policy: 'one_factor'
        # First-party client: no separate consent screen.
        consent_mode: 'implicit'
        redirect_uris:
          - 'https://$app:$port/oidc/callback'
        scopes: ['openid', 'profile', 'email', 'groups']
        claims_policy: 'kia'
        userinfo_signed_response_alg: 'none'
        token_endpoint_auth_method: 'client_secret_post'
EOF
    mv -f "$config.tmp" "$config"
}

# What Authelia runs with: its settings file. (As gateway.sh does for the gateway, since Docker
# Desktop's file watch misses later changes.) Its own name, so a link put there by a taken-over
# kia is never opened.
# shellcheck disable=SC2012  # one name we chose; macOS's find can't print an inode
stamp() { { cat "$config" 2>/dev/null; cat "$users" 2>/dev/null; } | if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi | cut -d' ' -f1; }

# On Linux Authelia notices a new settings file by itself; with Docker Desktop it doesn't, so
# when the settings changed since it started, it's restarted. kia keeps no login state in
# memory (it's in Authelia's database), so a logged-in person stays logged in.
refresh() {
    [ -s "$config" ] || return 0
    now=$(stamp)
    if [ "$now" = "$(cat "$loaded" 2>/dev/null)" ]; then return 0; fi
    if [ -n "$(docker compose ps -q --status running authelia 2>/dev/null)" ]; then
        if ! docker compose restart authelia >/dev/null 2>&1; then
            echo "frontdoor: couldn't restart Authelia: docker compose restart authelia" >&2
            return 0  # not marked as loaded, so the next refresh tries again
        fi
        echo "Restarted the front door with its new settings."
    fi
    printf '%s\n' "$now" >"$loaded.tmp"
    mv -f "$loaded.tmp" "$loaded"
}

case "$cmd" in
    list)
        if [ -s "$users" ] && grep -q '^  [a-z0-9-]\{1,\}:' "$users" 2>/dev/null; then
            sed -n 's/^  \([a-z0-9-]\{1,\}\):$/\1/p' "$users"
        else
            echo "No account can log in yet: scripts/frontdoor.sh account dad"
        fi
        ;;
    refresh)
        refresh
        ;;
    write)
        write
        refresh
        echo "Wrote $config."
        ;;
    account)
        hosts
        [ -s "$config" ] || die "set the front door up first: scripts/frontdoor.sh write"
        [ -n "$email" ] || email="$name@$domain"
        case "$email" in *@*.*) ;; *) die "$email isn't an email address (NAME@$domain)" ;; esac
        # The password is asked for on the terminal, never shown and never on a command line.
        printf 'Password for %s: ' "$name" >&2
        stty -echo 2>/dev/null || true
        IFS= read -r pw || true
        stty echo 2>/dev/null || true
        echo >&2
        [ -n "$pw" ] || die "no password given"
        [ ${#pw} -ge 8 ] || die "a password is at least 8 characters"
        pwhash=$(hash_password argon2 "$pw")
        case "$pwhash" in \$argon2*) ;; *) die "Authelia didn't return a hash for the password" ;; esac
        # One account for now, in both groups: admins (the stack's admin pages) and household
        # (kia). Roles split these in 0.8.2.
        cat >"$users.tmp" <<EOF
# kia's front-door accounts. scripts/frontdoor.sh account writes this; each password is a hash
# Authelia made, never the password itself.
users:
  $name:
    disabled: false
    displayname: '$name'
    password: '$pwhash'
    email: '$email'
    groups:
      - 'admins'
      - 'household'
EOF
        mv -f "$users.tmp" "$users"
        refresh
        echo "Set the account $name. It can log in at the front door once the stack is running."
        ;;
esac
