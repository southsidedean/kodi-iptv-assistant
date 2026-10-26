# Settings both servers share (see nginx.conf).
root /srv/output;
charset utf-8;
autoindex on;
server_tokens off;
# Serve only real files in output/, never what a link inside it points to.
disable_symlinks on;

# Compress the Dashboard's data (guide-preview.json is about 700 KB, 130 KB compressed).
gzip on;
gzip_types application/json text/css application/javascript;

# Clients should always re-check; the guide changes several times a day.
add_header Cache-Control "no-cache";
# always: nginx's own error pages (a 404, a 502 while kia restarts) get these too.
# kia's pages are never shown inside another site's page.
add_header X-Frame-Options "DENY" always;
# Channel names and shows come from other people's playlists and guides. kia escapes them,
# and if something got through anyway, a page could still load and send nothing outside
# kia itself (the secure page keeps your password in the browser while it's open).
add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Referrer-Policy "no-referrer" always;

# Don't serve files that a run is still writing.
location ~ \.tmp$ {
    return 404;
}

location ~ \.m3u8?$ {
    types { }
    default_type audio/x-mpegurl;
}

location ~ \.md$ {
    types { }
    default_type "text/plain; charset=utf-8";
}

# Pluto TV channels play through kia (kia.pluto), so they keep going across ad breaks.
# ^~ so the .m3u8 rule above doesn't take these addresses. Port 8781 isn't published.
location ^~ /pluto/ {
    resolver 127.0.0.11 valid=10s;
    set $kia http://kia:8781;
    proxy_pass $kia;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    # Only /api/ says whether a request came over HTTPS (nginx.conf). Cleared here, so an
    # address crafted to reach the API through this location can't claim it did.
    proxy_set_header X-Kia-Secure "";
    proxy_read_timeout 60s;
}

# kia's numbers for Prometheus (docs/metrics.md), answered by kia.
location = /metrics {
    resolver 127.0.0.11 valid=10s;
    set $kia http://kia:8781;
    proxy_pass $kia;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Kia-Secure "";
}
