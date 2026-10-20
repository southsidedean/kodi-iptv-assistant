# Using kia from scripts and other apps

Everything the status page shows, and everything it can change, is also available to scripts, home automation, and other apps. The status, playlists, and guides are files you can read on the plain HTTP port. The API under `/api/` reads and changes [profiles](profiles.md) and the password: reading works on either port, and changes work only on the secure port.

The examples use `192.168.1.20` for the kia machine; use yours.

## Files

| Address | What it is |
|---|---|
| `http://192.168.1.20:8780/status.json` | Whether kia is working, the channel counts, alerts, playlists, the certificate, and the settings. Its fields are listed in [how it works](how-it-works.md#statusjson). |
| `http://192.168.1.20:8780/profiles.json` | Each profile's `name`, `title`, playlist `file`, its own `guide` file, and `tv` and `radio` counts, plus `error` when the profiles file has a mistake |
| `http://192.168.1.20:8780/curated.m3u` | The full playlist |
| `http://192.168.1.20:8780/<name>.m3u` | A profile's playlist |
| `http://192.168.1.20:8780/guide.xml.gz` | The full guide (XMLTV, gzipped) |
| `http://192.168.1.20:8780/<name>.guide.xml.gz` | A profile's guide, with only its channels |
| `http://192.168.1.20:8780/guide-preview.json` | The next 14 hours of listings, for "On now": `generated` (when), and `channels`, each channel's ID with a list of `[start, stop, title]` (start and stop in Unix time). Channels without listings are left out. |
| `http://192.168.1.20:8780/report.json` | Every stream from the last check, with its result |

A file changes only when kia finishes writing it, so a script never reads half of one.

## The API

Requests and answers are JSON. An error comes back with an HTTP status code and a message people can read:

```json
{"error": "Wrong password."}
```

| Request | What it does | Needs |
|---|---|---|
| `GET /api/profiles` | Every profile's settings, plus the categories and source playlists to choose from, with channel counts | |
| `POST /api/preview` | How many channels a profile with the settings you send would have, and a few of their names | |
| `GET /api/channels?q=cnn` | Up to 25 channels whose name or ID contains the text: `id`, `name`, `group` | |
| `PUT /api/profiles/<name>` | Makes the profile, or replaces its settings | password, HTTPS |
| `DELETE /api/profiles/<name>` | Deletes the profile, its playlist, its guide, and its page | password, HTTPS |
| `POST /api/password` | Changes the password: send `{"new": "..."}` | password, HTTPS |

A profile's settings are the ones in [what a profile can say](profiles.md#what-a-profile-can-say), as JSON:

```json
{"title": "The kids", "groups": ["Kids", "Animation"], "radio": false, "renumber": true}
```

A change rewrites `profiles/profiles.toml`, makes the playlist and its guide, and updates the status page, all before the answer comes back. It returns the same contents as `profiles.json`.

### The password

Send the password in the `X-Kia-Password` header. [The secure status page](secure-page.md#the-password) explains where it is and how to change it. A wrong password gets `401` after a one-second wait. Changes sent to the plain HTTP port get `403`, even with the right password.

### Examples

kia's own certificate isn't one your computer trusts, so tell curl to trust that certificate with `--cacert tls/fullchain.pem`. That works on the kia machine, or with a copy of the file. (`-k` skips the check entirely; avoid it outside a quick test.)

Read the profiles:

```sh
curl -s http://192.168.1.20:8780/api/profiles
```

Make a news playlist for the kitchen:

```sh
curl -s --cacert tls/fullchain.pem -X PUT \
  -H "X-Kia-Password: $KIA_PASSWORD" -H "Content-Type: application/json" \
  -d '{"title": "Kitchen", "names": ["news", "weather"], "radio": false}' \
  https://192.168.1.20:8743/api/profiles/kitchen
```

Delete it:

```sh
curl -s --cacert tls/fullchain.pem -X DELETE -H "X-Kia-Password: $KIA_PASSWORD" \
  https://192.168.1.20:8743/api/profiles/kitchen
```

The certificate covers the names in `KIA_TLS_NAMES` (or `KIA_PUBLIC_URL`), so use one of those in the address.

### From a web page

The API answers only pages kia serves itself. A browser asks a site for permission before sending it an `X-Kia-Password` header from another site's page, and kia never gives that permission. kia also refuses any request whose `Origin` is a different address. To use the API from a page of your own, put the page in kia's `output` folder, or call the API from a script or home automation tool instead.

## Stability

The files and the API follow kia's version numbers: a field or request can be added in any release, and anything removed or changed is listed in the [changelog](../CHANGELOG.md) under that release. Scripts should ignore fields they don't know.
