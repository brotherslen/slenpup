#!/usr/bin/env bash
# Flash vanilla OpenWrt onto a GL.iNet GL-MT3000 (Beryl AX) running GL stock firmware.
#
# Run from a machine plugged into the Beryl's LAN port (or joined to its stock Wi-Fi).
# Stock firmware must already be through its first-run wizard so root has a password.
#
#   ./flash.sh                 # 25.12.5, router at 192.168.8.1
#   VER=24.10.8 ./flash.sh     # older branch
#   ROUTER=192.168.8.1 ./flash.sh
set -euo pipefail

VER=${VER:-25.12.5}
ROUTER=${ROUTER:-192.168.8.1}
BASE="https://downloads.openwrt.org/releases/${VER}/targets/mediatek/filogic"
IMG="openwrt-${VER}-mediatek-filogic-glinet_gl-mt3000-squashfs-sysupgrade.bin"
WORK="${WORK:-$(pwd)/fw}"

mkdir -p "$WORK"
cd "$WORK"

echo ">> Downloading $IMG"
curl -fL --retry 3 -o "$IMG" "$BASE/$IMG"
curl -fL --retry 3 -o sha256sums "$BASE/sha256sums"

echo ">> Verifying checksum"
line=$(grep -E "[ *]${IMG}\$" sha256sums) || { echo "!! $IMG not listed in sha256sums"; exit 1; }
if command -v sha256sum >/dev/null; then
  echo "$line" | sha256sum -c -
else
  echo "$line" | shasum -a 256 -c -
fi

# Stock firmware and the fresh OpenWrt install present different host keys on the
# same IP, so drop any old entry and accept the new one on first contact.
ssh-keygen -R "$ROUTER" >/dev/null 2>&1 || true
SSH=(ssh -o StrictHostKeyChecking=accept-new "root@$ROUTER")

echo ">> Checking target (you'll be asked for the GL admin password)"
model=$("${SSH[@]}" 'cat /tmp/sysinfo/board_name 2>/dev/null || cat /proc/device-tree/compatible | tr "\0" " "')
echo "   board: $model"
case "$model" in
  *mt3000*) ;;
  *) echo "!! This doesn't look like a GL-MT3000. Stopping."; exit 1 ;;
esac

echo ">> Uploading image"
"${SSH[@]}" 'cat > /tmp/fw.bin' < "$IMG"

echo ">> Testing image"
"${SSH[@]}" 'sysupgrade -T /tmp/fw.bin' || {
  echo "!! Stock firmware rejected the image. Update GL stock firmware to its latest"
  echo "   release first (GL admin panel > System > Upgrade), then rerun."
  exit 1
}

echo ">> Flashing without keeping settings (-n)"
# sysupgrade drops the SSH session when it takes over, so a nonzero exit here is normal.
"${SSH[@]}" 'sysupgrade -n /tmp/fw.bin' || true

cat <<EOF

The router is now writing flash and will reboot. Don't pull power.
Give it about 3 minutes. When the LED settles, the new OpenWrt install is at
192.168.1.1 with no root password. Renew your DHCP lease (or unplug and
replug the cable), then run ./setup.sh.
EOF
