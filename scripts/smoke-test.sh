#!/bin/sh
# End to end: kia's whole stack, one full refresh, and what it serves afterwards.
#
#   scripts/smoke-test.sh
#
# CI runs it on every pull request. It works on a clean copy of the committed code in a
# temporary folder, with its own ports, image name, and Compose project, so it never
# touches a kia you run (uncommitted changes aren't in the copy). The channel source is
# one test stream made with ffmpeg and served by kia's own web server, so no outside
# stream is needed; the refresh still fetches iptv-org/epg, as a real one does, but a fixed
# commit of it (epg_ref, below), so a change there can't block every pull request. Move it
# to iptv-org's newest commit with each release; the weekly upstream workflow tries the
# newest one meanwhile. SMOKE_EPG_REF=latest tries it here.
# Needs Docker (with Compose), git, curl, and python3.
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
port=${SMOKE_PORT:-18780}
https_port=${SMOKE_HTTPS_PORT:-18743}
project="kia-smoke-$$"
epg_ref=${SMOKE_EPG_REF:-5010ea56afbffb27d683092e18dbe927cd112fbe}  # iptv-org/epg, 2026-10-28
work=$(mktemp -d "${TMPDIR:-/tmp}/kia-smoke.XXXXXX")

compose() { docker compose -p "$project" "$@"; }
cleanup() {
    (cd "$work" && compose down -v --remove-orphans --rmi local >/dev/null 2>&1) || true
    docker image rm -f kia-smoke:test >/dev/null 2>&1 || true
    rm -rf "$work"
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM
fail() {
    echo "FAIL: $*" >&2
    (cd "$work" && compose ps && compose logs --tail 60 kia web) >&2 || true
    exit 1
}
ok() { echo "ok: $*"; }

echo "== A clean copy of the committed code in $work"
git -C "$here" archive HEAD | tar -x -C "$work"
cd "$work"
sed -e "s|^KIA_PUBLIC_URL=.*|KIA_PUBLIC_URL=http://localhost:$port|" \
    -e "s|^KIA_PORT=.*|KIA_PORT=$port|" \
    -e "s|^KIA_HTTPS_PORT=.*|KIA_HTTPS_PORT=$https_port|" \
    -e "s|^KIA_BIND=.*|KIA_BIND=127.0.0.1|" \
    -e "s|^KIA_SOURCES_FILE=.*|KIA_SOURCES_FILE=smoke.sources.txt|" \
    -e "s|^KIA_IMAGE=.*|KIA_IMAGE=kia-smoke:test|" \
    -e "s|^KIA_EPG_GIT_REF=.*|KIA_EPG_GIT_REF=$epg_ref|" \
    -e "s|^PUID=.*|PUID=$(id -u)|" \
    -e "s|^PGID=.*|PGID=$(id -g)|" \
    .env.example >.env
mkdir -p output state work vendor profiles tls playlists
printf '#EXTM3U\n#EXTINF:-1 tvg-id="Smoke.us" group-title="News",Smoke Test TV\nhttp://web/smoke.ts\n' >playlists/smoke.m3u
printf 'playlists/smoke.m3u smoke\n' >smoke.sources.txt

echo "== Build kia, and make a 20-second test stream with its ffmpeg"
compose build kia >/dev/null || fail "the image doesn't build"
compose run --rm --no-deps --entrypoint ffmpeg kia -v error -f lavfi -i testsrc=size=640x360:rate=25 \
    -f lavfi -i sine=frequency=440 -t 20 -c:v mpeg2video -c:a mp2 -f mpegts /data/output/smoke.ts \
    || fail "ffmpeg couldn't make the test stream"

echo "== Start the stack and wait until it's healthy"
compose up -d --quiet-pull 2>&1 | tail -n 20 || true
compose ps --status running --services | grep -q '^kia$' || fail "the stack doesn't start"
tries=0
until [ "$(compose ps --format '{{.Service}}={{.Health}}' | grep -c '=healthy')" -ge 2 ]; do
    tries=$((tries + 1))
    [ $tries -gt 60 ] && fail "kia and web weren't healthy after 3 minutes"
    sleep 3
done
ok "kia and web are healthy"

# A new install refreshes as soon as kia starts (it has no guide yet), possibly before the
# web server serving the test stream was up. Let that finish, then refresh again.
echo "== Wait for kia's own first refresh"
tries=0
until python3 -c "import json, sys, urllib.request
s = json.load(urllib.request.urlopen('http://localhost:$port/status.json', timeout=10))
sys.exit(0 if s.get('result') not in (None, 'running') else 1)" 2>/dev/null; do
    tries=$((tries + 1))
    [ $tries -gt 120 ] && fail "kia's first refresh didn't finish in 10 minutes"
    sleep 5
done
ok "the first refresh finished"

echo "== One full refresh, checked"
compose exec -T kia python3 -m kia run >"$work/run.log" 2>&1 || { cat "$work/run.log" >&2; fail "the refresh failed"; }
tail -5 "$work/run.log"
if grep -q "without Node's sandbox" "$work/run.log"; then
    cat "$work/run.log" >&2
    fail "iptv-org's grabber would run without Node's sandbox"
fi
if [ ! -f vendor/epg/.kia/grab.cjs ] || [ ! -f vendor/epg/.kia/api-load.cjs ]; then
    fail "iptv-org's scripts weren't compiled for the sandbox"
fi
ok "the refresh finished, with iptv-org's scripts compiled for the sandbox"
if [ "$epg_ref" != latest ]; then
    got=$(git -C vendor/epg rev-parse HEAD 2>/dev/null || true)
    [ "$got" = "$epg_ref" ] || fail "iptv-org/epg is at ${got:-nothing}, not $epg_ref"
fi
ok "iptv-org/epg is at $epg_ref"

echo "== What kia serves"
python3 - "$port" "$https_port" <<'EOF' || fail "see above"
import gzip, json, ssl, sys, urllib.error, urllib.request
import xml.etree.ElementTree as ET

port, https_port = sys.argv[1], sys.argv[2]
base = f"http://localhost:{port}"
problems = []


def get(path, url=None, method="GET", context=None):
    req = urllib.request.Request(url or base + path, method=method, data=b"{}" if method != "GET" else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20, context=context) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def check(ok, what):
    print(("ok: " if ok else "FAIL: ") + what)
    if not ok:
        problems.append(what)


status, body = get("/curated.m3u")
check(status == 200 and b"Smoke Test TV" in body, "the playlist has the test channel")
status, body = get("/guide.xml.gz")
root = ET.fromstring(gzip.decompress(body)) if status == 200 else None
check(root is not None and root.find("channel[@id='Smoke.us']") is not None
      and root.find("programme[@channel='Smoke.us']") is not None, "the guide is valid XML, with the channel and its listings")
s = json.loads(get("/status.json")[1])
check(s.get("result") == "ok" and (s.get("channels") or {}).get("tv") == 1, f"status.json: result {s.get('result')}, channels {s.get('channels')}")
metrics = get("/metrics")[1].decode()
check("kia_last_refresh_success 1" in metrics and 'kia_channels{kind="tv"} 1' in metrics, "/metrics reports the refresh and the channel")
for page in ("index.html", "playlists.html", "sources.html", "guide.html", "system.html"):
    status, body = get("/" + page)
    check(status == 200 and b"kia" in body, f"the Dashboard's {page} answers")
insecure = ssl.create_default_context()
insecure.check_hostname, insecure.verify_mode = False, ssl.CERT_NONE  # kia's own self-signed certificate
status, body = get(None, f"https://localhost:{https_port}/api/sources", context=insecure)
sources = json.loads(body) if status == 200 else {}
check(any(p.get("name") == "smoke" for p in sources.get("playlists", [])), "the secure page's API answers")
check(sources.get("secure") is True, "kia believes requests that come through nginx's secure port")
check(json.loads(get("/api/sources")[1]).get("secure") is False, "and not the ones on the plain port")
check(get("/api/sources", method="PUT")[0] in (403, 404), "changes are refused on the plain http port")
sys.exit(1 if problems else 0)
EOF

python3 -c "import os, sys; sys.exit(os.stat('tls/privkey.pem').st_mode & 0o007)" \
    || fail "kia's certificate key is readable by other users of this computer"
ok "the certificate key is private (nginx reads it through your group)"
[ "$(compose ps --format '{{.Service}}={{.Health}}' | grep -c '=healthy')" -ge 2 ] || fail "a container is no longer healthy"
echo "== All good"
