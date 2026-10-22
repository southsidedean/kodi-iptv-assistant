# Metrics for Prometheus

kia publishes its numbers at `/metrics`, in the format [Prometheus](https://prometheus.io/) reads: `http://192.168.1.20:8780/metrics`, with your kia's address. Prometheus can then keep their history, and [Grafana](https://grafana.com/) can graph them and warn you when something changes, such as a nightly refresh that failed or a channel source that lost half its streams.

The numbers are the ones the Dashboard shows, so nothing on the page is secret, and it needs no password.

## Collecting them

Add kia to `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: kia
    scrape_interval: 1m
    static_configs:
      - targets: ["192.168.1.20:8780"]
```

Most of the numbers change once a night or every few hours, so once a minute is plenty. Without Docker, kia answers on its own port (8780) the same way.

## What's there

| Metric | What it is |
|---|---|
| `kia_info{version}` | Always 1; the label is kia's version |
| `kia_start_time_seconds` | When kia started (Unix time) |
| `kia_refresh_running` | 1 while the nightly refresh runs |
| `kia_last_refresh_success` | 1 if the last refresh finished, with or without alerts |
| `kia_last_refresh_alerts` | 1 if it raised alerts |
| `kia_last_refresh_start_time_seconds`, `kia_last_refresh_end_time_seconds`, `kia_last_refresh_duration_seconds` | When the last refresh ran, and how long it took |
| `kia_next_refresh_time_seconds` | When the next one starts |
| `kia_guide_updated_time_seconds` | When the guide was last rebuilt (it's refreshed every few hours) |
| `kia_channels{kind}` | Channels in the full playlist: `tv` and `radio` |
| `kia_channels_with_guide` | Channels with guide data |
| `kia_guide_coverage_channels{level}` | TV channels with a `full`, `partial`, or no (`none`) guide |
| `kia_source_streams{source,status}` | Streams in each channel source at the last check: `ok`, `flaky`, `dead` |
| `kia_playlist_channels{playlist,kind}` | Channels in each playlist |
| `kia_alerts` | Alerts from the last refresh |
| `kia_pluto_channels_playing` | [Pluto TV channels](how-it-works.md#pluto-tv) kia is playing now |
| `kia_pluto_viewers{channel,name}` | Players watching each of them |
| `kia_pluto_sessions_total`, `kia_pluto_rewinds_total`, `kia_pluto_errors_total`, `kia_pluto_fallbacks_total` | What the Pluto relay has dealt with since kia started |
| `kia_pluto_pieces_total`, `kia_pluto_bytes_total` | Video it has passed on |
| `kia_process_cpu_seconds_total` | Processor time kia itself used |
| `kia_children_cpu_seconds_total` | Processor time of the programs kia ran: ffprobe for stream checks, ffmpeg for Pluto, and the guide grabber |
| `kia_process_resident_memory_bytes` | Memory kia uses |

Times are Unix times in seconds, so `time() - kia_guide_updated_time_seconds` is the guide's age. The `_total` numbers start again from 0 when kia restarts; Prometheus's `rate()` and `increase()` allow for that.

## Some useful queries

- The last refresh failed: `kia_last_refresh_success == 0`
- No refresh in over a day: `time() - kia_last_refresh_end_time_seconds > 26 * 3600`
- The guide is old: `time() - kia_guide_updated_time_seconds > 8 * 3600`
- Working streams per source: `kia_source_streams{status="ok"}`
- Pluto relay problems per hour: `increase(kia_pluto_errors_total[1h])`
- kia's share of a processor core: `rate(kia_process_cpu_seconds_total[5m]) + rate(kia_children_cpu_seconds_total[5m])`

kia's own [alerts](alerts.md) already tell you about a failed refresh; these are for when you keep the rest of your home's numbers in Prometheus too.
