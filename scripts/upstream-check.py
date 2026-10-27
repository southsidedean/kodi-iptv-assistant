"""Does kia still work with the newest iptv-org/epg? (CI runs this every week.)

iptv-org/epg changes every day. This fetches its latest version the way kia does, installs
it, compiles its scripts for Node's sandbox, and downloads two days of guide from a couple
of sites in the sandbox. It fails if the sandbox can't be used (iptv-org moved its scripts,
say), so kia would quietly fall back to running their code without it, or if no site
gives any listings.

    python3 scripts/upstream-check.py [folder]    # default: a temporary folder
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kia import grab
from kia.config import EpgConfig

# Sites that have been steady for a long time; one working is enough.
SITES = ("tvpassport.com", "tvtv.us", "pluto.tv")


def main() -> int:
    base = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="kia-upstream-"))
    messages: list[str] = []

    def log(msg: str) -> None:
        messages.append(msg)
        print(msg, flush=True)

    flags = grab.node_permission()
    if flags is None:
        print("FAIL: this Node.js has no sandbox (node --permission)")
        return 1
    print(f"Node's sandbox: {' '.join(flags)}")
    cfg = EpgConfig(repo_dir=base / "vendor" / "epg", work_dir=base / "work", runner="local", native_grabbers=False, days=2)
    grab.ensure_repo(cfg, update=False, log=log)
    missing = [s for s in grab.SCRIPTS if not (cfg.repo_dir / ".kia" / f"{s}.cjs").exists()]
    if missing:
        print(f"FAIL: iptv-org's scripts weren't compiled for the sandbox: {', '.join(missing)}")
        return 1

    channels = cfg.work_dir / "channels"
    channels.mkdir(parents=True, exist_ok=True)
    for site in SITES:
        found = sorted((cfg.repo_dir / "sites" / site).glob("*.channels.xml"))
        if not found:
            log(f"{site}: not in iptv-org/epg any more")
            continue
        picks = re.findall(r"<channel [^>]*>[^<]*</channel>", found[0].read_text())[:2]
        (channels / f"{site}.channels.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<channels>\n' + "\n".join(picks) + "\n</channels>\n"
        )
    results = grab.grab_all(cfg, None, log)
    if any("without Node's sandbox" in m for m in messages):
        print("FAIL: the grabber ran without Node's sandbox")
        return 1
    listings = {r.site: r.output.read_text().count("<programme") if r.output.exists() else 0 for r in results}
    print("Listings per site:", listings)
    for r in results:
        if not listings[r.site]:
            tail = r.log.read_text()[-1500:] if r.log.exists() else "(no log: its grabber didn't start)"
            print(f"--- {r.site} log ---\n{tail}")
    if not any(listings.values()):
        print("FAIL: no site gave any listings")
        return 1
    print("OK: the newest iptv-org/epg works in Node's sandbox")
    return 0


if __name__ == "__main__":
    sys.exit(main())
