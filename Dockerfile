# kodi-iptv-assistant: stream checker + EPG builder.
# Bundles Python (kia), ffmpeg (stream probing), and Node.js + git (iptv-org/epg grabber),
# so the host only needs Docker. Builds for amd64 and arm64 (Raspberry Pi 4/5).
# Pinned to the versions this release was tested with; override with KIA_NODE_IMAGE in .env.
ARG NODE_IMAGE=node:22.23.3-alpine3.24
FROM ${NODE_IMAGE}

RUN apk add --no-cache python3 ffmpeg git tzdata

COPY src /app/src
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    KIA_EPG_RUNNER=local \
    HOME=/tmp

# Config, playlists, and all generated data live in the mounted project folder.
WORKDIR /data
ENTRYPOINT ["python3", "-m", "kia"]
CMD ["daemon"]
