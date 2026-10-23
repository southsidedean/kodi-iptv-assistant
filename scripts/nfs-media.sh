#!/bin/sh
# Mount shares from a NAS or another server (NFS) on this computer, for Plex in the stack
# (docs/cookbooks/plex-box.md#media-on-a-nas-or-another-server). Each share appears as a
# folder in one media folder, which KIA_MEDIA_DIR then gives to Plex. They come back after
# every restart, mounted when first used, so a server that's off doesn't hold up startup.
#
#   sudo scripts/nfs-media.sh add movies nas.local:/volume1/movies     read-only
#   sudo scripts/nfs-media.sh add photos nas.local:/volume1/photos --rw
#   sudo scripts/nfs-media.sh remove movies
#   scripts/nfs-media.sh list
#
# The media folder: /Users/Shared/media on a Mac, /mnt/media on Linux (--base DIR for
# another; use the same one every time).
set -eu

die() { echo "nfs-media: $*" >&2; exit 1; }
OS=$(uname -s)
case "$OS" in
    Darwin) BASE=/Users/Shared/media ;;  # not /Volumes: macOS removes folders it doesn't know there
    Linux) BASE=/mnt/media ;;
    *) die "this works on macOS and Linux." ;;
esac

case "${1:-}" in -h | --help | "") sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; esac
cmd=$1; shift
name="" share="" rw=no
while [ $# -gt 0 ]; do
    case "$1" in
        --rw) rw=yes ;;
        --base) [ $# -gt 1 ] || die "--base needs a folder"; BASE=${2%/}; shift ;;
        -h | --help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) die "unknown option: $1 (see --help)" ;;
        *) if [ -z "$name" ]; then name=$1; elif [ -z "$share" ]; then share=$1; else die "too many arguments"; fi ;;
    esac
    shift
done
case "$BASE" in /*) ;; *) die "--base must be a full path, such as /mnt/media" ;; esac
# /etc/fstab and the automounter's maps split their lines at spaces.
case "$BASE$share" in *[[:space:]]*) die "the folder and the share can't have spaces in them" ;; esac

# Read-only unless --rw: Plex only needs to read. soft: a server that's off makes reads
# fail instead of freezing what reads them.
if [ "$OS" = Darwin ]; then
    MAP=/etc/auto_kia_media
    opts="-fstype=nfs,nfsvers=3,resvport,soft,intr,rsize=65536"
else
    opts="soft,noatime,_netdev,nofail,x-systemd.automount,x-systemd.requires=network-online.target,x-systemd.mount-timeout=30"
fi

need_root() { [ "$(id -u)" = 0 ] || die "run it with sudo: sudo $0 $cmd ..."; }
check_name() {
    [ -n "$name" ] || die "give the share a name, such as movies (see --help)"
    case "$name" in *[!A-Za-z0-9._-]* | .*) die "a name has letters, digits, dots, dashes, and underscores: $name" ;; esac
}

# The lines this script added (Linux: marked in /etc/fstab).
mine() {
    if [ "$OS" = Darwin ]; then
        if [ -f "$MAP" ]; then cat "$MAP"; fi
    else
        grep " # kia-media$" /etc/fstab || true
    fi
}

backup() { cp "$1" "$1.before-kia-media-$(date +%Y%m%d-%H%M%S)"; }

case "$cmd" in
    list)
        lines=$(mine)
        [ -n "$lines" ] || { echo "No shares yet. Add one with: sudo $0 add NAME SERVER:/path"; exit 0; }
        # name and share: the map's first and third fields; fstab's mount point and first.
        if [ "$OS" = Darwin ]; then pairs=$(echo "$lines" | awk '{print $1, $3}'); else pairs=$(echo "$lines" | awk '{n = $2; sub(".*/", "", n); print n, $1}'); fi
        echo "$pairs" | while read -r n s; do
            state="answering"
            [ -n "$(ls -A "$BASE/$n" 2>/dev/null)" ] || state="empty: is the server on?"
            echo "$BASE/$n  <-  $s  ($state)"
        done
        ;;
    add)
        need_root; check_name
        case "$share" in *:/*) ;; *) die "the share looks like SERVER:/path, such as nas.local:/volume1/movies" ;; esac
        if [ "$OS" = Linux ] && ! command -v mount.nfs >/dev/null 2>&1; then
            die "this computer can't mount NFS yet. Install it first: sudo apt install nfs-common (Debian, Ubuntu, Raspberry Pi OS) or sudo dnf install nfs-utils (Fedora)."
        fi
        mode=ro; [ "$rw" = yes ] && mode=rw
        if [ "$OS" = Darwin ]; then
            touch "$MAP"
            grep -v "^${name}[[:space:]]" "$MAP" > "$MAP.new" || true
            printf '%s\t-%s,%s\t%s\n' "$name" "$mode" "${opts#-}" "$share" >> "$MAP.new"
            mv "$MAP.new" "$MAP" && chmod 644 "$MAP"
            mkdir -p "$BASE"
            if ! grep -q "^${BASE}[[:space:]].*${MAP#/etc/}" /etc/auto_master; then
                ! grep -q "[[:space:]]${MAP#/etc/}[[:space:]]" /etc/auto_master || die "/etc/auto_master already puts ${MAP#/etc/} in another folder; give that one as --base"
                ! grep -q "^${BASE}[[:space:]]" /etc/auto_master || die "/etc/auto_master already mounts something else at $BASE; choose another folder with --base"
                backup /etc/auto_master
                printf '%s\t\t%s\t-nosuid,nobrowse\n' "$BASE" "${MAP#/etc/}" >> /etc/auto_master
            fi
            automount -c
        else
            mkdir -p "$BASE/$name"
            backup /etc/fstab
            grep -v "[[:space:]]${BASE}/${name}[[:space:]].* # kia-media$" /etc/fstab > /etc/fstab.new || true
            printf '%s %s nfs %s,%s 0 0 # kia-media\n' "$share" "$BASE/$name" "$mode" "$opts" >> /etc/fstab.new
            cat /etc/fstab.new > /etc/fstab && rm /etc/fstab.new
            systemctl daemon-reload
            systemctl start "$(systemd-escape --path --suffix=automount "$BASE/$name")"
        fi
        if [ -n "$(ls -A "$BASE/$name" 2>/dev/null)" ]; then
            echo "OK: $share is at $BASE/$name ($(find "$BASE/$name" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ') entries)."
            echo "For Plex in the stack, set KIA_MEDIA_DIR=$BASE in .env, then: docker compose up -d plex"
            echo "In Plex, the share is the folder /data/$name."
        else
            echo "Saved, but $BASE/$name is empty: check that the server is on, that it shares $share with this computer, and that the path is right." >&2
            exit 1
        fi
        ;;
    remove)
        need_root; check_name
        if [ "$OS" = Darwin ]; then
            if [ ! -f "$MAP" ] || ! grep -q "^${name}[[:space:]]" "$MAP"; then die "there's no share named $name ($0 list)"; fi
            grep -v "^${name}[[:space:]]" "$MAP" > "$MAP.new" || true
            mv "$MAP.new" "$MAP" && chmod 644 "$MAP"
            umount "$BASE/$name" 2>/dev/null || true
            automount -c
        else
            grep -q "[[:space:]]${BASE}/${name}[[:space:]].* # kia-media$" /etc/fstab || die "there's no share named $name ($0 list)"
            umount "$BASE/$name" 2>/dev/null || true
            backup /etc/fstab
            grep -v "[[:space:]]${BASE}/${name}[[:space:]].* # kia-media$" /etc/fstab > /etc/fstab.new || true
            cat /etc/fstab.new > /etc/fstab && rm /etc/fstab.new
            systemctl stop "$(systemd-escape --path --suffix=automount "$BASE/$name")" 2>/dev/null || true
            systemctl daemon-reload
            rmdir "$BASE/$name" 2>/dev/null || true
        fi
        echo "Removed $name. Plex keeps its library until you remove it there."
        ;;
    *) die "say add, remove, or list (see --help)" ;;
esac
