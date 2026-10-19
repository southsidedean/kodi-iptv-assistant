# Playlists for people and devices

kia's main playlist, `curated.m3u`, has every working channel. That's often more than one person or one TV needs: the kids' tablet doesn't need the news, and the living room TV doesn't need shopping channels. Profiles let you make smaller playlists from the full one, each with its own address.

Every profile uses the same program guide and the same channel numbers as the full list, so a channel is number 45 everywhere.

## Making your first profiles

1. In the kia folder, copy the example file:

   ```sh
   cp profiles.example.toml profiles.local.toml
   ```

   Updating kia never changes your copy.

2. Open `profiles.local.toml` in a text editor. It starts with three profiles you can keep, change, or delete:

   ```toml
   [kids]
   title = "Kids"
   groups = ["Kids", "Animation", "Family"]
   radio = false

   [living-room]
   title = "Living room"
   exclude_groups = ["Shop", "Religious"]

   [radio]
   title = "Radio only"
   tv = false
   ```

   The name in square brackets becomes the playlist's address, so `[kids]` is `http://<your address>:8780/kids.m3u`. Use lowercase letters, numbers, and dashes.

3. Wait for the next nightly run, or make the playlists now:

   ```sh
   docker compose exec kia python3 -m kia check
   ```

4. Open the status page at `http://<your address>:8780/`. The Playlists card lists each profile with its channel count and its address, with a Copy button.

5. On the device, use the profile's address as the **M3U playlist URL** in IPTV Simple Client (or your player), instead of `curated.m3u`.

## What a profile can say

| Setting | What it does | Example |
|---|---|---|
| `title` | The name shown on the status page | `"The kids"` |
| `groups` | Include channels in these categories | `["Kids", "Animation"]` |
| `sources` | Include channels from these playlists, by the names in your sources list | `["samsung-us"]` |
| `names` | Include channels whose names contain these words | `["news", "weather"]` |
| `channels` | Include these channels by ID, whatever else the profile says | `["CNN.us@SD"]` |
| `exclude_groups` | Leave out these categories | `["Shop"]` |
| `exclude_names` | Leave out channels whose names contain these words | `["kids"]` |
| `tv`, `radio` | Include TV channels, radio stations (both are included unless you say `false`) | `radio = false` |

If a profile names something to include (`groups`, `sources`, or `names`), it starts with just those channels; otherwise it starts with all of them. Anything in `exclude_groups` or `exclude_names` is then taken out. `channels` adds specific channels at the end, even ones the other rules left out.

Categories are the ones in the full playlist, such as "News", "Kids", or "Sports"; the stream check report (`report.html`) shows the category of every channel. Upper and lower case don't matter. `names` and `exclude_names` are patterns, so `"news"` also matches "Fox News Now" and "NewsNation"; to match a whole name exactly, write `"^CNN$"`.

## More examples

A news and weather playlist for the kitchen:

```toml
[kitchen]
names = ["news", "weather"]
radio = false
```

Everything from the Samsung TV Plus list except shopping:

```toml
[samsung]
sources = ["samsung-us"]
exclude_groups = ["Shop"]
```

A short list of favorites, picked by ID (the IDs are in `report.html`):

```toml
[favorites]
channels = ["CNN.us@SD", "PBSKids.us@SD"]
```

## If something goes wrong

If the profiles file has a mistake, such as a missing bracket or a misspelled setting, kia keeps the profile playlists it made last time and shows the problem on the status page. The full playlist updates as usual. Fix the file, and the next run picks it up.

A profile you delete from the file is removed from the server at the next run.
