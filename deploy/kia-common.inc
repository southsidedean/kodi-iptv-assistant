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
# kia's pages are never shown inside another site's page.
add_header X-Frame-Options "DENY";

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
    proxy_read_timeout 60s;
}
