# Settings both servers share (see nginx.conf).
root /srv/output;
charset utf-8;
autoindex on;
server_tokens off;
# Serve only real files in output/, never what a link inside it points to.
disable_symlinks on;

# Clients should always re-check; the guide changes several times a day.
add_header Cache-Control "no-cache";

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
