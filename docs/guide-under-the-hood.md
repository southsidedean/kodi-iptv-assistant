# How the guide gets built

What kia does, file by file, to turn your channel list into a TV guide: how it finds a guide source for each channel, downloads the listings, picks the best, and keeps the guide filled in between nights. For the short version, see [how it works](how-it-works.md). Read this one to see why a channel has the guide it has, or none.

## The pieces

| Piece | Where | What it is |
|---|---|---|
| iptv-org/epg | `vendor/epg/` | iptv-org's guide project: a list of guide sites, the channels each one has, and a downloader for each site. kia keeps a copy and updates it every night |
| The channel list | `output/curated.m3u` | The channels that passed tonight's check, each with its `tvg-id` |
| Merged channels | `work/aliases.json` | For each channel kept from more than one playlist, the IDs it has in the others |
| Playlists' own guides | `work/playlist-guides.json` | The guides playlists name in their first line (`x-tvg-url`), and which of their channels you have |
| Each site's channels | `work/channels/<site>.channels.xml` | What kia asks each guide site for: the site's channel IDs, written under your `tvg-id`s |
| The match | `work/mapping.json` | Each channel's guide sources, best first |
| Downloads | `work/guides/<site>.xml` | Each site's listings, from its last good download |
| Logs | `work/logs/<site>.log` | What each site's download printed |
| The choice | `work/guide-choices.json` | Each channel's chosen source, and how much of the next 24 hours it fills |
| The guide | `output/guide.xml`, `guide.xml.gz` | What players read |
| Reports | `output/mapping-report.html`, `guide-report.html` | Which source each channel matched, and how much of the next day each one fills |

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/guide-build-dark.svg">
  <img alt="How the guide gets built. Tonight's channel list and iptv-org's list of guide sites go into matching, which writes a channel list per site. Downloading runs each site's downloader, kia's own readers for i.mjh.nz, epgshare01, kink.nl, KEXP, and NTS, and the playlists' own guides, into one file per source. Building picks, for each channel, the source with the most of the next 24 hours, and writes guide.xml.gz, the profiles' guides, and the reports. Every 4 hours, a quick refresh downloads the quick sources again and builds the guide again." src="../images/diagrams/guide-build-light.svg">
</picture>

## Matching (`kia map`)

Kodi and other players find a channel's listings by its ID, so every channel needs a guide source that lists it under some ID kia can translate. kia first updates its copy of iptv-org/epg (or uses the copy it has, if GitHub can't be reached), then reads every site's channel list in it, about 180,000 channels in all. Sites that iptv-org marks broken (🔴 in its `SITES.md`), and those in `exclude_sites`, are left out.

For each channel, in this order:

1. The same ID: a site that lists `CNN.us@East` matches a channel with that `tvg-id`, ignoring upper and lower case.
2. Another feed of the channel: when nothing lists `CNN.us@East` but a site lists `CNN.us@West` or `CNN.us`, that site's listings are used, a few hours off at worst.
3. The channel's IDs in other playlists: when kia merged a channel that several playlists carry ([how it works](how-it-works.md#1-check-streams-kia-check)), it tries each of its IDs, in the same two ways.
4. Its name: only for channels nothing matched by ID, and only an exact name in the same country (`News 12 New Jersey` and `News12 New Jersey` count as the same; `News12+ CT` and `News 12 CT` don't). A name that belongs to two different channels on a site isn't used, since there's no telling which is yours. `ignore_name_matches` turns a wrong one off.
5. A looser name, with words like HD, TV, and Channel left out, is only listed in `mapping-report.html` as a possible match. It's used only if you add the channel to `accept_name_matches`.

Radio stations named exactly KINK, KEXP, NTS Radio 1, or NTS Radio 2 get their station's own schedule. A channel whose playlist names its own guide gets that guide too, placed first.

A Pluto TV channel's stream address carries Pluto's own ID for it (`jmp2.uk/plu-<id>`, or `/pluto/<id>.m3u8` once it plays through kia), and i.mjh.nz's Pluto guides list channels by that ID. So every Pluto channel also gets i.mjh.nz's guide for its country, by that ID, after its other sources, whatever name or `tvg-id` the playlist gives it. The country comes from the `tvg-id` (`.us` is the US guide, and `.uk` the one i.mjh.nz calls `gb`), for the 14 countries i.mjh.nz covers. It takes the place of any other i.mjh.nz guide the channel matched by name (Plex's or Samsung's), since both would end up in i.mjh.nz's file under the same ID, their programs overlapping. This one doesn't count toward `max_sites_per_channel`.

epgshare01.online, a free guide service that one person runs, publishes a guide file a "feed": a country's (`US2`, `UK1`) or a service's (Distro TV, TBN+, Peacock). iptv-org lists its channels under feed names it has since changed, and mostly without IDs. So kia reads epgshare01's own channel lists instead, for the countries in your playlists (and the services that carry US channels, when you have US ones), and leaves iptv-org's out. Each channel's name comes from its ID there (`Bounce.TV.us2` is Bounce TV), with the feed's country, and is matched by exact name like any other site's. A name that two of epgshare01's feeds list (as two channels, or one channel twice) isn't matched there, since there's no telling which one is yours. kia keeps the lists for a day, and uses last time's when epgshare01 can't be reached. Local US stations (`US_LOCALS1`, 58 MB a night) aren't read. `exclude_sites = ["epgshare01.online"]` turns it off.

Each channel keeps up to three sources (`max_sites_per_channel`). When there are more, the sites in `prefer_sites` go first, then the sites that cover the most of your channels: those tend to be the big, well-kept ones. kia writes one channel list per site into `work/channels/`, with each site's channel IDs written under your `tvg-id`s, so the downloaded listings already carry the IDs your playlist uses.

## Downloading (`kia grab`)

kia updates iptv-org's copy once a night, fetching only the newest version (or the one you pinned). It reinstalls iptv-org's Node.js packages only when their list changed, without running any of their install scripts. iptv-org's downloader runs in Node's sandbox, where it can read only its own folder and the channel list, write only its output, and sees none of kia's settings ([security](../SECURITY.md#the-guide-downloader)).

Then, four sites at a time (`parallel_sites`):

- Most sites: iptv-org's downloader for that site, for 3 days (`days`), with up to 5 connections at once (`site_connections` and `site_delay_ms` change that per site).
- i.mjh.nz (Pluto TV, Plex, Samsung TV Plus, Roku): kia reads the site's ready-made guide files itself, as they arrive, keeping only your channels. That takes seconds, where iptv-org's downloader takes about 25 minutes and over 2 GB of memory.
- epgshare01.online: kia downloads only the feeds your channels use, once a night, and keeps your channels, as with i.mjh.nz.
- kink.nl, KEXP, and NTS: kia reads KINK's schedule page and KEXP's and NTS's public APIs itself.
- Playlists' own guides: kia downloads each guide a playlist names, unzipping it if it's gzip, and keeps your channels. A playlist can name many guides (one names about 100, over 300 MB in all), so kia notes which addresses had any of your programs, for the quick refreshes below.

Each download goes to `work/guides/.staging/` first and replaces the site's file only when it worked, so a site that fails tonight keeps last night's listings. A download that runs past `site_time_limit` (30 minutes) is stopped, with the same result. For i.mjh.nz, epgshare01, and the playlists' guides, which come in several files, a failed file doesn't lose its channels either: kia keeps those channels' programs still to come from last time.

A site that fails three nights in a row (`site_fail_runs`) raises an alert. When a site's name couldn't be looked up, kia asks public DNS servers too, to tell a site that's gone from a DNS problem on your network ([DNS checks](how-it-works.md#dns-checks-and-alerts)).

## Building (`kia guide`)

kia reads every downloaded file and counts, for each channel and each source, how many of the next 24 hours its listings fill. Programmes with no title don't count (some sites send only times), and neither does a source with nothing in the next 24 hours. Each channel takes the source that fills the most hours, then the one with the most programs; on a tie, the earlier one in its list from matching wins. Only one source is used per channel, so its listings never overlap.

Then kia writes `guide.xml` and `guide.xml.gz`:

- An entry for every channel in the list, with its name and logo.
- Each channel's programs from its source, each once. iptv-org's downloaders read a site a day at a time, so a show that runs past midnight comes back on the next day's page; kia writes a channel's program at a given start time only once.
- For a channel with no source, two-hour blocks with the channel's name (`placeholders`, `placeholder_hours`), so it still shows in the guide. Radio stations get none unless `KIA_RADIO_PLACEHOLDERS` is on.
- For a channel whose listings stop early (some guides reach only 11 hours ahead), name blocks for the rest of the guide.

Each file is written to a temporary name and renamed into place, so a player never reads half a guide. kia then writes each [profile](profiles.md)'s guide with only its channels, `guide-preview.json` for the Dashboard's **On now**, and `guide-report.html`. A channel counts as fully covered when its source fills 20 or more of the next 24 hours. If the number of channels with listings falls by a quarter or more since the last build or refresh (`drop_percent`), kia raises an alert.

## Between nights (`kia refresh-guide`)

Every 4 hours (`KIA_GUIDE_REFRESH_HOURS`), kia downloads the quick sources again, the ones it reads itself (but not epgshare01, which changes once a day): i.mjh.nz, kink.nl, KEXP, NTS, and the playlists' own guides (only the addresses that had your programs). It takes seconds. Then it builds the guide again, with last night's files from every other site. Streams aren't checked, and nothing is matched again: that waits for the night.

## With an upstream server

With `KIA_UPSTREAM_URL`, kia skips matching and downloading. It downloads the other kia server's guide, and builds its own from it, with the same rules, keeping only the channels in its own list. If the upstream can't be reached, kia matches and downloads by itself as usual, or uses the last copy with `KIA_UPSTREAM_FALLBACK=cached`. The quick refreshes download the upstream's guide again.

## When a channel's guide is wrong or missing

**Why?** next to the channel on its playlist's page, or `kia doctor channel <name>`, reads the files below for that channel and explains them. `kia doctor guide` does it for the whole guide: its age, the sites it comes from, sites on a streak of failed downloads (with their logs' errors), and the channels that matched a site that sent nothing or matched none. To read them yourself:

| What you see | Where to look |
|---|---|
| Only the channel's name, all day | `mapping-report.html`: is the channel matched at all? If it's under possible matches, check it and add it to `accept_name_matches` |
| Matched, but still only its name | `guide-report.html` for its source, then `work/logs/<site>.log` for that site's download |
| Another channel's programs | A wrong name match: add the channel's `tvg-id` to `ignore_name_matches` |
| A few hours off | Matched to another feed of the channel (step 2 above); `mapping-report.html` counts those |
| Listings stop in the evening | The source reaches only hours ahead; the quick refreshes fill it in as the day goes on |

[Troubleshooting](troubleshooting.md#channels-but-the-guide-is-empty) has more.
