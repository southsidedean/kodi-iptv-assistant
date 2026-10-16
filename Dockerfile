# kodi-iptv-assistant: stream checker + EPG builder.
# Bundles Python (kia), ffmpeg (stream probing), and Node.js + git (iptv-org/epg grabber),
# so the host only needs Docker. Builds for amd64 and arm64 (Raspberry Pi 4/5).
FROM node:22-alpine

RUN apk add --no-cache python3 ffmpeg git tzdata

COPY src /app/src
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    KIA_EPG_RUNNER=local \
    NODE_OPTIONS=--max-old-space-size=4096 \
    HOME=/tmp

# Config, playlists, and all generated data live in the mounted project folder.
WORKDIR /data
ENTRYPOINT ["python3", "-m", "kia"]
CMD ["daemon"]
