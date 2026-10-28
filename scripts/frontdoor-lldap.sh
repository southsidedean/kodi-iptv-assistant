#!/bin/sh
# Runs inside the lldap container (scripts/frontdoor.sh loads this and hands it to the container's
# shell). LLDAP's admin API (port 17170) has no published port on the host; only the gateway
# network reaches it, so the account work happens in here. Secrets and parameters arrive on stdin,
# one per line, so no password ever reaches a command line (argv shows in a process list on the
# host). The order is: op, admin password, login name, email, admin (yes/no), the new password.
# Group and user strings go into GraphQL as variables, so no value is spliced into a query and
# nothing needs escaping. The query text holds $u, $g, $n, $x as GraphQL variable names, in single
# quotes so the shell leaves them alone: that is deliberate, not a missed expansion (SC2016).
# Prints the account list for the list op.
# shellcheck disable=SC2016
set -eu
IFS= read -r op      || true
IFS= read -r ADMINPW || true
IFS= read -r uname   || true
IFS= read -r email   || true
IFS= read -r admin   || true
IFS= read -r NEWPW   || true
usergroups=""
# LLDAP binds IPv4 only; "localhost" would try IPv6 first and be refused, so use 127.0.0.1.
base=http://127.0.0.1:17170
api() { wget -q -O- --header="Content-Type: application/json" "$@"; }
tok=$(api --post-data="$(printf '{"username":"admin","password":"%s"}' "$ADMINPW")" "$base/auth/simple/login" \
        | sed 's/.*"token":"\([^"]*\)".*/\1/')
[ -n "$tok" ] || { echo "login-failed" >&2; exit 3; }
gql() { api --header="Authorization: Bearer $tok" --post-data="$1" "$base/api/graphql"; }

# The numeric id of a group by its exact name, or empty if there's no such group. Fields come
# back in the order the query asks for them (id then displayName), so one object is one line.
groups_json=$(gql '{"query":"{groups{id displayName}}"}')
id_of() { echo "$groups_json" | grep -o '{"id":[0-9]*,"displayName":"[^"]*"}' | grep "\"displayName\":\"$1\"}" | sed 's/.*"id":\([0-9]*\).*/\1/' | head -n 1; }
# Make a group if it's missing (admins and household are kia's own, not LLDAP's built-in ones).
ensure_group() {
    if [ -z "$(id_of "$1")" ]; then
        gql "$(printf '{"query":"mutation($n:String!){createGroup(name:$n){id}}","variables":{"n":"%s"}}' "$1")" >/dev/null
        groups_json=$(gql '{"query":"{groups{id displayName}}"}')
    fi
}
# The names of the groups the user is in, one per line.
user_groups() { gql "$(printf '{"query":"query($u:String!){user(userId:$u){groups{displayName}}}","variables":{"u":"%s"}}' "$uname")" | grep -o '"displayName":"[^"]*"' | sed 's/.*:"\(.*\)"/\1/'; }
# Does a user with this login name already exist?
user_exists() { gql '{"query":"{users{id}}"}' | grep -o '"id":"[^"]*"' | sed 's/.*:"\(.*\)"/\1/' | grep -Fxq "$uname"; }
# Put the user in a group, or take them out, only when it needs changing.
set_member() {  # set_member GROUP yes|no   (reads $usergroups)
    gid=$(id_of "$1")
    if echo "$usergroups" | grep -Fxq "$1"; then has=yes; else has=no; fi
    if [ "$2" = yes ] && [ "$has" = no ]; then
        gql "$(printf '{"query":"mutation($u:String!,$g:Int!){addUserToGroup(userId:$u,groupId:$g){ok}}","variables":{"u":"%s","g":%s}}' "$uname" "$gid")" >/dev/null
    elif [ "$2" = no ] && [ "$has" = yes ]; then
        gql "$(printf '{"query":"mutation($u:String!,$g:Int!){removeUserFromGroup(userId:$u,groupId:$g){ok}}","variables":{"u":"%s","g":%s}}' "$uname" "$gid")" >/dev/null
    fi
}

case "$op" in
    list)
        # Every login account except admin (the account store's own bind user), with the admin
        # ones marked.
        gql '{"query":"{users{id}}"}' | grep -o '"id":"[^"]*"' | sed 's/.*:"\(.*\)"/\1/' | while IFS= read -r u; do
            [ "$u" = admin ] && continue
            if gql "$(printf '{"query":"query($u:String!){user(userId:$u){groups{displayName}}}","variables":{"u":"%s"}}' "$u")" | grep -q '"displayName":"admins"'; then
                echo "$u (admin)"
            else
                echo "$u"
            fi
        done
        ;;
    create)
        ensure_group admins
        ensure_group household
        if ! user_exists; then
            gql "$(printf '{"query":"mutation($x:CreateUserInput!){createUser(user:$x){id}}","variables":{"x":{"id":"%s","email":"%s","displayName":"%s"}}}' "$uname" "$email" "$uname")" >/dev/null
        fi
        # LLDAP's own tool runs the OPAQUE exchange that sets the password; the password reaches
        # it only in the environment, never on a command line.
        LLDAP_USER_PASSWORD="$NEWPW" /app/lldap_set_password --base-url "$base" --token "$tok" --username "$uname" >/dev/null
        usergroups=$(user_groups)
        set_member household yes
        set_member admins "$admin"
        ;;
    set-admin)
        user_exists || { echo "no-such-user" >&2; exit 4; }
        ensure_group admins
        usergroups=$(user_groups)
        set_member admins "$admin"
        ;;
    remove)
        user_exists || { echo "no-such-user" >&2; exit 4; }
        gql "$(printf '{"query":"mutation($u:String!){deleteUser(userId:$u){ok}}","variables":{"u":"%s"}}' "$uname")" >/dev/null
        ;;
esac
