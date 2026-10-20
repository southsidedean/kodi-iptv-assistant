# Alerts

kia runs on its own at night, so nobody is watching when something goes wrong. Most problems need no attention: a dead stream drops out of the list and a broken guide site is replaced by another. Alerts are for the problems that break things for you and won't fix themselves, such as your network's DNS failing or the nightly refresh crashing. Without an alert, you'd find out days later when the guide goes blank.

Alerts are off until you choose where to send them. Until then they're written to `state/alerts.log`, and the last run's alerts are listed on kia's status page at `http://<your address>:8780/`.

## What triggers an alert

| Event | On by default | What it means |
|---|---|---|
| `dns_local` | yes | Names that fail to look up on your network work on public DNS, so your router, ad-blocker, or DNS server is failing. Kodi will have the same problem. |
| `run_failed` | yes | The nightly refresh failed or crashed. The channel list and guide stop updating until it's fixed. |
| `channels_drop` | yes | Working channels fell sharply since the last run (25% by default). Usually a playlist that broke upstream or a bad internet connection during the run. |
| `guide_drop` | yes | Channels with guide data fell sharply. A major guide source probably broke. |
| `site_failing` | yes | One guide site failed 3 runs in a row. You hear about it once per streak, not every night. |
| `pluto_fallback` | no | kia couldn't open a session of its own with Pluto TV for 5 minutes, so a Pluto channel is playing without kia's fix and may freeze at ad breaks. You hear about each channel at most once an hour. |
| `daily_summary` | no | A short "all done" message after each run, e.g. "1174 working channels, 541 with guide data". It's handy at first, to see that everything works. |

If you remove playlists or exclude guide sites yourself, the next run may report a drop. That's expected, and the message says so. What to do about each alert is in [troubleshooting](troubleshooting.md#alerts).

## Getting alerts on your phone (ntfy)

[ntfy](https://ntfy.sh) is a free notification service with apps for iOS and Android. There's no account to create.

1. Install the ntfy app and subscribe to a topic. Make the name long and random (for example `kia-7f3k9q2m8x`), because anyone who knows it can read your messages.
2. In `.env`, set `KIA_NTFY_URL=https://ntfy.sh/kia-7f3k9q2m8x` (with your topic name).
3. Apply the change and send a test:

   ```sh
   docker compose up -d
   docker compose exec kia python3 -m kia test-alert
   ```

The test notification should arrive on your phone within a few seconds. DNS and failed-run alerts arrive as high priority.

Alert messages contain only host names, channel IDs, and counts. If you'd rather not use the public ntfy server, you can [run your own](https://docs.ntfy.sh/install/) and point `KIA_NTFY_URL` at it.

## Other ways to be notified

`KIA_ALERT_COMMAND` runs any shell command for each alert, inside the kia container, with `$KIA_ALERT_EVENT`, `$KIA_ALERT_TITLE`, and `$KIA_ALERT_MESSAGE` set. The container includes `curl`, so anything with a simple web address for posting messages works. You can use ntfy and a command together. Email isn't supported directly, because the container has no mail program.

## Choosing events

`KIA_ALERT_EVENTS` in `.env` picks which events notify you, comma-separated. For example, to add the nightly summary to the defaults:

```ini
KIA_ALERT_EVENTS=dns_local,run_failed,channels_drop,guide_drop,site_failing,daily_summary
```

The thresholds are in `kia.toml` under `[alerts]`: `drop_percent` (default 25) and `site_fail_runs` (default 3).

Alerts for the events you've turned on are also recorded in `state/alerts.log`, whether or not notifications are set up, and a run that raised one exits with code 3, in case you monitor kia with other tools.
