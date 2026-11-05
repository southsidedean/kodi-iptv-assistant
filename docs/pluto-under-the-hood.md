# The Pluto TV relay under the hood

What kia does, step by step, when a player opens a Pluto TV channel: which channels go through kia, how it keeps a session with Pluto, how it turns Pluto's playlist into one that players can follow through ad breaks, and what it does when Pluto misbehaves. For why kia plays Pluto channels at all, see [how it works](how-it-works.md#pluto-tv). Read this one when a Pluto channel acts up, or to see what the System page's numbers mean.

## Which channels go through kia

When kia writes the playlists, it points each Pluto channel at itself, at `KIA_PUBLIC_URL/pluto/<channel id>.m3u8`. A Pluto channel is one whose stream address is:

- a `jmp2.uk/plu-<id>` link, which iptv-org's lists and most others use, or
- an address on Pluto's own stream server.

Some Samsung TV Plus channels are Pluto channels underneath. During the nightly check, kia follows the link of each Samsung entry whose name or ID says Pluto, once, and if it ends at Pluto's stream server, that channel goes through kia too.

Without `KIA_PUBLIC_URL`, or with `KIA_PLUTO_RELAY=false`, the playlists keep Pluto's own links. kia answers only for Pluto channels in its own playlists, so its address can't be used to play any other.

With Docker, kia's web server passes `/pluto/` on to kia. Without Docker, kia answers on its own port.

## A session with Pluto

Nothing happens until a player asks for a channel. Then kia opens a session the way Pluto's web player does, asking Pluto to put the ads in itself. Pluto answers with a session that holds the channel's playlists at several qualities, and kia takes the best one. Pluto also says how long the session should last, usually 8 hours. If it marks the session as suspicious, kia notes it on the System page.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/pluto-session-dark.svg">
  <img alt="A Pluto TV channel through kia, step by step. A player asks kia for the channel's playlist. kia opens a session with Pluto, with Pluto's ads in it, and reads the session's playlist. At most every 2 seconds, kia takes the new pieces in time order and holds back repeats and rewinds, then gives the player the last 6 pieces under kia's own numbers. The player asks kia for a piece. kia fetches it and its key from Pluto, and ffmpeg decrypts it and moves its clock on from the piece before. The player gets the piece, which kia keeps for other viewers. Near the end of Pluto's time limit, kia opens a new session at the next ad or part of the show." src="../images/diagrams/pluto-session-light.svg">
</picture>

## From Pluto's playlist to kia's

A live playlist lists the last few pieces of the stream, a few seconds of video each, and players ask for it again and again to find new ones. kia does the same with Pluto's, at most every 2 seconds, and only while a player is asking. From each look, kia takes only the pieces that are new:

- In time order: each piece carries the time it belongs to. A piece no later than the last one kia took is one it already has, so it's skipped.
- No rewinds: every ad, and every part of a show, is a clip with numbered pieces. When Pluto sends a piece of a clip that's no further on than the last one kia took of it, Pluto has gone back. kia holds those pieces back instead of playing a minute of the show again, for up to 10 seconds, which is the pause you may see. After that it plays what Pluto sends, and notes "Pluto kept going back" as a problem. It doesn't wait longer because its playlist stands still while it holds back, and Plex stops asking for more after 15 to 20 seconds of that, then plays what it has over and over. If Pluto goes back and sends nothing new, kia opens a fresh session (at most one every 20 seconds). A fresh session started in the middle of an ad break begins the break again, so if that one goes back too, kia doesn't open another until something new has played.
- No repeats: the same piece twice in a row is skipped.

kia numbers the pieces it takes with its own numbers, which only go up, and keeps the last 60. The playlist it gives players lists the last 6.

## Each piece, on one clock

When a player asks for a piece, kia fetches it from Pluto, along with its decryption key, since most are encrypted. Every clip starts its own clock. ffmpeg decrypts the piece and moves its timestamps so it starts exactly where the piece before it ended. It only copies the video and sound, without re-encoding, so this is light work (the one exception is below). kia's clock starts at 10 seconds and only ever moves forward, so players never see it start over.

Inside a piece, the sound keeps its small offset from the picture. A few ads have sound out by a second or more; kia lines that up with the picture instead. A piece that holds more than Pluto listed is cut to its listed length.

Some ads come with no sound at all, sometimes for half a minute or more. Passed on as they are, the sound would have a hole the picture doesn't, and players and Plex close it up: after the break, the sound plays against the wrong picture until the channel is started again. So kia puts silence in their place, as long as the piece and in the same sound format (Pluto's is AAC at 48 kHz, two channels), made by ffmpeg. That's the one thing kia encodes itself, and it's a few milliseconds of work. Before calling a piece soundless, kia looks at all of it, in case its sound starts late. The tiny sliver some ads end with, which declares sound but holds none, gets the same. These pieces are counted (`soundless` in `pluto.json`, `kia_pluto_soundless_total` in [metrics](metrics.md)) but aren't a problem, so they don't show as one on the Dashboard.

When a piece has sound that won't copy, kia passes on the picture with silence instead, or alone if even that fails, rather than failing the piece. That shouldn't happen, so it's worth knowing about: kia counts every such piece (`silent` in `pluto.json`, `kia_pluto_silent_total` in [metrics](metrics.md)), and for a piece of a second or more also says so on the Dashboard and in its log, and saves it to the debug folder below when that's on.

Everyone watching a channel shares its session and its pieces: each piece is fetched and copied once, and kia keeps the last 12 for players that are a little behind. A piece that failed is tried again on the next request.

Only https addresses are fetched, and at most 16 MB each, since the addresses come from Pluto's playlists. ffmpeg and ffprobe get none of kia's settings ([security](../SECURITY.md#the-containers)).

Now and then Pluto sends a clip ffmpeg can't copy, and the piece fails. To look into one, make a folder named `pluto-debug` in kia's `state` folder: while it's there, kia saves the next few pieces it can't copy, or whose sound won't copy, into it, each as the piece itself and a small file of its details (where it came from, its key, and the error), so you can examine a real failure later. It keeps at most 25, holds only what came from Pluto, and does nothing when the folder isn't there. Delete the folder to stop.

## Fresh sessions

Pluto wants a new session before the old one runs out. A new session starts at a different point, and switching mid-show would show as a jump. So from half an hour before the end (or the last quarter of a short session), kia waits for the next clip change, an ad or the next part of the show, and switches there. If none comes by one and a half times the session's length, it switches anyway.

## When Pluto won't play along

| What happens | What kia does |
|---|---|
| Pluto won't open a session | Plays Pluto's plain `jmp2.uk` link instead, which may freeze at breaks. It tries its own session again every 5 minutes. After 5 minutes on the plain link, it raises the `pluto_fallback` [alert](alerts.md), at most once an hour per channel |
| Pluto's playlist doesn't load, or isn't a playlist | Opens a new session and tries again |
| A piece or key doesn't load, or ffmpeg can't copy it | Answers that request with an error; the player asks again |
| Pluto answers with an error or times out | Waits a second and tries once more |

Every problem goes to kia's log, as `Pluto <channel id>: ...`, and to the channel's list on the System page.

## Limits

- 20 Pluto channels at once. A player that opens a 21st gets an error until one is let go.
- A channel is let go 2 minutes after the last request.
- Only a player opening the channel's playlist starts a channel. A request for a piece of a channel that isn't playing gets a "reload the playlist" error, so stray requests can't hold places.

## What you can see

- The System page, on the secure Dashboard, lists each channel playing: viewers (devices that asked in the last 30 seconds), whether kia is on its own session or the plain link, the session's age and the hours until its refresh, and counts of rewinds, sessions, errors, and fallbacks, with the last few problems.
- `pluto.json` has the same, for scripts ([fields](how-it-works.md#plutojson)). kia rewrites it every minute, and when a player asks (at most every 15 seconds).
- `kia doctor pluto` (and **Ask kia what's wrong** on the Dashboard) explains it: the channels playing now, any on the plain link and for how long, their last problems, and the `pluto_fallback` alerts of the last week.
- `/metrics` has the channels playing, the viewers on each, and running totals of pieces, bytes, sessions, rewinds, errors, and fallbacks ([metrics](metrics.md)). The update and stack scripts read it to see whether anyone's watching before they restart kia.
