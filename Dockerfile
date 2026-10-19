# kodi-iptv-assistant: stream checker + EPG builder.
# Bundles Python (kia), ffmpeg (stream probing), and Node.js + git (iptv-org/epg grabber),
# so the host only needs Docker. Builds for amd64 and arm64 (Raspberry Pi 4/5).
# Tracks the latest Node.js LTS release. To pin a version, set KIA_NODE_IMAGE in .env.
ARG NODE_IMAGE=node:lts-alpine
FROM ${NODE_IMAGE}

# curl is for alert commands (e.g. ntfy notifications); see [alerts] in kia.toml.
# openssl makes the secure status page's self-signed certificate (kia.tls).
RUN apk add --no-cache python3 ffmpeg git tzdata curl openssl

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
ENTRYPOINT ["python3", "-m", "kia"]
CMD ["daemon"]
