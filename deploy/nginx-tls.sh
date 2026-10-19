#!/bin/sh
# Runs as the web container starts (nginx's /docker-entrypoint.d/). Waits for kia to make
# the certificate on first start, then reloads nginx whenever the certificate changes:
# kia's renewals, or your own certificate copied into tls/.
set -eu
cert=/etc/kia-tls/fullchain.pem
key=/etc/kia-tls/privkey.pem
n=0
until [ -s "$cert" ] && [ -s "$key" ]; do
    n=$((n + 1))
    if [ "$n" -gt 120 ]; then
        echo "kia: no certificate in tls/ after 2 minutes; is the kia container running?" >&2
        exit 1
    fi
    sleep 1
done
stamp() { stat -c %Y "$cert" "$key" 2>/dev/null | tr '\n' ' '; }
(
    last=$(stamp)
    while sleep 60; do
        now=$(stamp)
        if [ "$now" != "$last" ]; then
            last=$now
            nginx -t -q && nginx -s reload && echo "kia: certificate changed; nginx reloaded"
        fi
    done
) &
