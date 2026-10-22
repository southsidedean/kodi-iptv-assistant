# kodi-iptv-assistant: stream checker + EPG builder.
# Bundles Python (kia), ffmpeg (stream probing), and Node.js + git (iptv-org/epg grabber),
# so the host only needs Docker. Builds for amd64 and arm64 (Raspberry Pi 4/5).
#
# Built on Wolfi (Chainguard's free, rolling base), whose packages get security fixes
# quickly. That matters most for ffmpeg, which reads streams from anywhere on the internet.
# KIA_BASE_IMAGE in .env pins the base by digest (scripts/pin-versions.sh). Wolfi names
# Node.js by its major version: KIA_NODE_VERSION (default 24, the current LTS release).
ARG BASE_IMAGE=cgr.dev/chainguard/wolfi-base:latest
FROM ${BASE_IMAGE}
ARG NODE_VERSION=24

# curl is for alert commands (e.g. ntfy notifications); see [alerts] in kia.toml.
# openssl makes the secure Dashboard's self-signed certificate (kia.tls).
# apk upgrade and the newest npm pick up security fixes released since the base image was
# built (the image scan in CI fails on fixable high or critical CVEs).
# The node user (1000) is the one compose.yaml's PUID/PGID default to.
RUN apk upgrade --no-cache \
    && apk add --no-cache python-3.14 "nodejs-${NODE_VERSION}" npm ffmpeg git tzdata curl openssl \
    && adduser -D -u 1000 node \
    && npm install -g npm@latest \
    && npm cache clean --force \
    && rm -rf /root/.npm /tmp/*

COPY src /app/src
# The container's own files are read-only (see compose.yaml), so npm keeps its cache in
# work/, which is writable.
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    KIA_EPG_RUNNER=local \
    HOME=/tmp \
    NPM_CONFIG_CACHE=/data/work/.npm \
    NPM_CONFIG_UPDATE_NOTIFIER=false

# Settings and playlists come from the project folder (read-only); results go to the
# output/, state/, work/, and vendor/ folders inside it.
WORKDIR /data
# Not root, even when started without compose.yaml (which runs it as PUID:PGID).
USER node
ENTRYPOINT ["python3", "-m", "kia"]
CMD ["daemon"]
