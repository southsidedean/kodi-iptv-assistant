# Setups

kia works the same way everywhere. What changes is who can reach it and how much work the machine should do. Each recipe below lists the `.env` lines to change from `.env.example`.

## Home server

One always-on machine (a Linux box, NAS, or mini PC) serves every Kodi device in the house, including ones that can't run anything themselves, like Fire TV, Android TV, or Apple TV.

```ini
KIA_PUBLIC_URL=http://192.168.1.20:8780   # this machine's address on your network
KIA_BIND=0.0.0.0                          # reachable by other devices
```

Give the machine a fixed address (a DHCP reservation in your router) so the URL in Kodi keeps working.

## Same machine as Kodi

A desktop, laptop, or Linux box that both runs kia and plays TV. Nothing is exposed to the network.

```ini
KIA_PUBLIC_URL=http://localhost:8780
KIA_BIND=127.0.0.1
KIA_RUN_ON_START=true      # computers that sleep at night refresh when kia starts
```

The nightly run uses a fair amount of CPU, network, and memory for up to an hour, so keep `KIA_RUN_AT` at a time you're not watching. A missed run (the computer was asleep) happens within a minute of waking.

## Laptop that travels (with a home server)

The laptop tests streams itself, because what plays depends on where you are. It copies the guide from your home server when it can reach it, which saves the long guide download. Away from home, it builds its own guide (or keeps the last copy; see below).

```ini
KIA_PUBLIC_URL=http://localhost:8780
KIA_BIND=127.0.0.1
KIA_RUN_ON_START=true
KIA_UPSTREAM_URL=http://192.168.1.20:8780   # your home server
KIA_UPSTREAM_FALLBACK=cached                # away from home: reuse the last guide (or "grab" a fresh one)
```

Use the same `sources.txt` as the home server so the guide covers the same channels. Kodi on the laptop points at `http://localhost:8780/curated.m3u`.

## Raspberry Pi

A Pi 4 or 5 (64-bit OS, 4 GB or more) works as a home server or alongside Kodi (e.g. LibreELEC with its Docker add-on). Use any recipe above, plus:

```ini
KIA_PARALLEL_SITES=1
KIA_CHECK_CONCURRENCY=8
KIA_MEMORY_LIMIT=2g
```

Runs take longer. With one site at a time, memory use peaks around 0.7 GB. Use a good SD card or, better, a USB SSD.

## Without Docker

You can run kia directly. You need Python 3.11+, ffmpeg, git, and Node.js 22+ (for the guide grabber).

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/kia daemon --serve --run-now   # refresh daily at 04:00 and serve on 127.0.0.1:8780
```

Settings go in `kia.toml` (set `runner = "local"` under `[epg]`). Most `.env` settings also work as environment variables; the schedule is `--at HH:MM` instead of `KIA_RUN_AT`, and the memory limit and image settings only apply to Docker. Add `--bind 0.0.0.0` to serve other devices. On Windows, use `.venv\Scripts\kia` instead.

Install scripts for macOS, Linux, and Windows are planned.
