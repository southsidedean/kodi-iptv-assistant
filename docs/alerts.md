# Alerts

kia runs on its own at night, so nobody is watching when something goes wrong. Most problems need no attention: a dead stream drops out of the list and a broken guide site is replaced by another. Alerts are for the problems that break things for you and won't fix themselves, such as your network's DNS failing or the nightly refresh crashing. Without an alert, you'd find out days later when the guide goes blank.

Alerts are off until you choose where to send them. Until then they're written to `state/alerts.log`, and the last run's alerts are listed on kia's Dashboard at `http://<your address>:8780/`.

## What triggers an alert

| Event | On by default | What it means |
|---|---|---|
| `dns_local` | yes | Names that fail to look up on your network work on public DNS, so your router, ad-blocker, or DNS server is failing. Kodi will have the same problem. |
| `run_failed` | yes | The nightly refresh failed or crashed. The channel list and guide stop updating until it's fixed. |
| `channels_drop` | yes | Working channels fell sharply since the last run (25% by default). Usually a playlist that broke upstream or a bad internet connection during the run. |
| `guide_drop` | yes | Channels with guide data fell sharply. A major guide source probably broke. |
| `site_failing` | yes | One guide site failed 3 runs in a row. You hear about it once per streak, not every night. |
| `part_down` | yes | A part of the stack you chose, such as Plex, Threadfin, or the gateway, hasn't answered kia's check for 10 minutes. You hear about it once, and again when it answers. Restarts and updates take less time, so they don't set it off. |
| `guessing` | yes | Someone got the Dashboard's password or kia's MCP token wrong 10 times in a row, from one address. Often it's a device with an old password or token saved; the alert names the address. kia slows the tries down either way. |
| `playlist_lan` | yes | A playlist from the internet pointed kia at your own network: a stream's address, or where it led (a redirect, an HLS piece), was on your network. kia didn't test those streams. You hear about each playlist once, until a night it doesn't. |
| `pluto_fallback` | no | kia couldn't open a session of its own with Pluto TV for 5 minutes, so a Pluto channel is playing without kia's fix and may freeze at ad breaks. You hear about each channel at most once an hour. |
| `assistant_asks` | no | An AI assistant asked kia to do something, such as test a stream, which waits for you to approve it on the Dashboard ([asking kia to act](mcp.md#asking-kia-to-act)). Tapping it on ntfy opens the Dashboard. A notice, not an alert. |
| `daily_summary` | no | A short "all done" message after each run, e.g. "1174 working channels, 541 with guide data". It's handy at first, to see that everything works. |
| `new_channels` | no | Channels new to your playlists, once a night after the run, naming up to 10 ([new and gone channels](#new-and-gone-channels)). A notice, not an alert. |
| `channels_gone` | no | Channels gone from your playlists because their streams stopped working, once a night after the run. A notice, not an alert. |

Each alert's title has the kia's name, such as `kia (den): nightly run failed`, so with more than one kia sending to the same place you know which one it is. The name is this computer's unless you change it on the Dashboard's **System** page or with `KIA_NAME` in `.env` ([settings](configuration.md)). On ntfy, tapping the alert opens that kia's Dashboard.

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

## Email

kia can send alerts by email through your email provider's server. Set these in `.env`, with your own details (they're already in the file, empty; fill those in, because Docker Compose uses the last copy of a line):

```ini
KIA_SMTP_HOST=smtp.gmail.com
KIA_SMTP_USER=you@gmail.com
KIA_SMTP_PASSWORD=abcd efgh ijkl mnop
KIA_EMAIL_TO=you@gmail.com
```

Then apply the change and send a test, as for ntfy:

```sh
docker compose up -d
docker compose exec kia python3 -m kia test-alert
```

Most providers, Gmail included, don't accept your normal password from programs like kia. They want an app password, which you make in your account's security settings (for Gmail, it needs 2-step verification on). Some common servers:

| Provider | `KIA_SMTP_HOST` | Notes |
|---|---|---|
| Gmail | `smtp.gmail.com` | An [app password](https://support.google.com/accounts/answer/185833) |
| Outlook.com, Hotmail | `smtp-mail.outlook.com` | Microsoft may require an app password or turn this off for some accounts |
| iCloud Mail | `smtp.mail.me.com` | An [app-specific password](https://support.apple.com/en-us/102654); `KIA_EMAIL_FROM` must be your iCloud address |
| Fastmail | `smtp.fastmail.com` | An app password |

These all use port 587 with STARTTLS, kia's default. For a server on port 465, set `KIA_SMTP_SECURITY=ssl`. `KIA_EMAIL_TO` can list several addresses, separated by commas. Emails come from `KIA_SMTP_USER` unless you set `KIA_EMAIL_FROM`.

The settings view on the Dashboard shows only whether email is set up, never the addresses or the password.

## Other ways to be notified

`KIA_ALERT_COMMAND` runs any shell command for each alert, inside the kia container, with `$KIA_ALERT_EVENT`, `$KIA_ALERT_TITLE`, and `$KIA_ALERT_MESSAGE` set. The container includes `curl`, so anything with a simple web address for posting messages works. In `.env`, put the command in single quotes, such as `KIA_ALERT_COMMAND='curl -d "$KIA_ALERT_MESSAGE" https://example.com/hook'`: without them, Docker fills in `$KIA_ALERT_MESSAGE` itself, from `.env`, before kia ever sees it, and it comes out empty. You can use ntfy, email, and a command together.

## New and gone channels

Free playlists change. To hear about new channels, add `new_channels` to your events ([choosing events](#choosing-events)), and `channels_gone` for the ones that stop working. After each nightly run you get at most one message of each, such as "kia: 3 new channels" with their names (up to 10, then a count).

A channel is new when it hasn't been in your playlists for 30 days (`history_days` in `kia.toml`), so one that drops out for a night or two and comes back isn't new again. A channel is gone when its stream stopped working past kia's grace nights (`grace_failures`, 2 by default). Channels you remove on the Dashboard aren't counted as gone. On kia's first night, everything is new, so kia says nothing; it starts the night after.

To hear only about some channels, add filters under `[alerts]` in `kia.toml`:

```toml
[alerts]
new_channels_with_guide = true                  # only channels with guide data
new_channels_categories = ["News", "Sports"]    # only these categories
```

The Dashboard's **Playlists** page lists the channels new and gone in the last week under **New this week**, with or without the notices. An AI assistant can ask the same with `kia_whats_new` ([kia's MCP server](mcp.md)).

## Choosing events

`KIA_ALERT_EVENTS` in `.env` picks which events notify you, comma-separated. For example, to add the nightly summary to the defaults:

```ini
KIA_ALERT_EVENTS=dns_local,run_failed,channels_drop,guide_drop,site_failing,part_down,guessing,playlist_lan,daily_summary
```

The thresholds are in `kia.toml` under `[alerts]`: `drop_percent` (default 25) and `site_fail_runs` (default 3).

Alerts for the events you've turned on are also recorded in `state/alerts.log`, whether or not notifications are set up, and a run that raised one exits with code 3, in case you monitor kia with other tools. Notices (`daily_summary`, `assistant_asks`, `new_channels`, `channels_gone`) aren't alerts, so they're neither.
