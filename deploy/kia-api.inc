# The Dashboard's editor, answered by kia (port 8781 isn't published).
# The resolver finds kia again after it restarts with a new address.
resolver 127.0.0.11 valid=10s;
set $kia http://kia:8781;
proxy_pass $kia;
proxy_set_header Host $http_host;
client_max_body_size 64k;
