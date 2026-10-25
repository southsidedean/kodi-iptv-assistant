# The Dashboard's editor, answered by kia (port 8781 isn't published).
# The resolver finds kia again after it restarts with a new address.
resolver 127.0.0.11 valid=10s;
set $kia http://kia:8781;
proxy_pass $kia;
proxy_set_header Host $http_host;
# Who sent it, for slowing down wrong passwords per address. Set here, so a header the
# browser sent can't pick someone else's address (or a new one for every guess).
proxy_set_header X-Real-IP $remote_addr;
client_max_body_size 64k;
