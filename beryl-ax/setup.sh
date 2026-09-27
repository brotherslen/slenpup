#!/usr/bin/env bash
# First-boot setup for a freshly flashed GL-MT3000 (Beryl AX) on vanilla OpenWrt.
#
# Run from a machine on the Beryl's LAN port right after flash.sh. It prompts for
# anything not set in the environment, pushes one config script over SSH, then
# reboots the router onto its new LAN address.
#
#   ./setup.sh
#   WIFI_SSID=beryl COUNTRY=US ZONENAME=America/Chicago TZ_POSIX='CST6CDT,M3.2.0,M11.1.0' ./setup.sh
#   EXTRAS=0 ./setup.sh        # skip package installs (no internet on WAN yet)
set -euo pipefail

ROUTER=${ROUTER:-192.168.1.1}
HOSTNAME_=${HOSTNAME_:-beryl}
LAN_IP=${LAN_IP:-192.168.8.1}
COUNTRY=${COUNTRY:-US}
ZONENAME=${ZONENAME:-UTC}
TZ_POSIX=${TZ_POSIX:-UTC0}
EXTRAS=${EXTRAS:-1}
PUBKEY=${PUBKEY:-}

ask() { # ask VAR "prompt" [silent]
  local __v=${!1:-}
  while [ -z "$__v" ]; do
    if [ "${3:-}" = silent ]; then read -rsp "$2: " __v; echo; else read -rp "$2: " __v; fi
  done
  printf -v "$1" %s "$__v"
}
q() { printf "'%s'" "$(printf %s "$1" | sed "s/'/'\\\\''/g")"; }

ask ROOT_PASSWORD "New root password" silent
ask WIFI_SSID "Wi-Fi name (SSID)"
ask WIFI_KEY "Wi-Fi password (8+ chars)" silent
[ ${#WIFI_KEY} -ge 8 ] || { echo "!! Wi-Fi password must be at least 8 characters"; exit 1; }

if [ -z "$PUBKEY" ]; then
  for k in ~/.ssh/id_ed25519.pub ~/.ssh/id_ecdsa.pub ~/.ssh/id_rsa.pub; do
    [ -f "$k" ] && { PUBKEY=$(cat "$k"); break; }
  done
fi

ssh-keygen -R "$ROUTER" >/dev/null 2>&1 || true

ssh -o StrictHostKeyChecking=accept-new "root@$ROUTER" sh -s <<EOF
set -e

ROOT_PASSWORD=$(q "$ROOT_PASSWORD")
WIFI_SSID=$(q "$WIFI_SSID")
WIFI_KEY=$(q "$WIFI_KEY")
PUBKEY=$(q "$PUBKEY")

echo ">> root password"
printf '%s\n%s\n' "\$ROOT_PASSWORD" "\$ROOT_PASSWORD" | passwd root >/dev/null

if [ -n "\$PUBKEY" ]; then
  echo ">> SSH key"
  grep -qxF "\$PUBKEY" /etc/dropbear/authorized_keys 2>/dev/null || echo "\$PUBKEY" >> /etc/dropbear/authorized_keys
  chmod 600 /etc/dropbear/authorized_keys
fi

echo ">> system"
uci set system.@system[0].hostname=$(q "$HOSTNAME_")
uci set system.@system[0].zonename=$(q "$ZONENAME")
uci set system.@system[0].timezone=$(q "$TZ_POSIX")

echo ">> Wi-Fi"
for r in \$(uci show wireless | sed -n 's/^wireless\.\([^.=]*\)=wifi-device\$/\1/p'); do
  band=\$(uci -q get wireless.\$r.band || true)
  uci set wireless.\$r.country=$(q "$COUNTRY")
  uci set wireless.\$r.disabled=0
  [ "\$band" = 5g ] && uci set wireless.\$r.htmode=HE80
  iface=default_\$r
  uci -q get wireless.\$iface >/dev/null || { echo "   \$r: no \$iface section, skipped"; continue; }
  uci set wireless.\$iface.ssid="\$WIFI_SSID"
  uci set wireless.\$iface.encryption=sae-mixed
  uci set wireless.\$iface.key="\$WIFI_KEY"
  uci set wireless.\$iface.network=lan
  uci set wireless.\$iface.mode=ap
  uci -q delete wireless.\$iface.disabled || true
  echo "   \$r (\$band) -> \$iface"
done

if [ $(q "$EXTRAS") = 1 ]; then
  echo ">> packages"
  PKGS="travelmate luci-app-travelmate luci-proto-wireguard"
  if command -v apk >/dev/null; then
    apk update && apk add \$PKGS || echo "!! package install failed; is WAN plugged into a network with internet?"
  else
    opkg update && opkg install \$PKGS || echo "!! package install failed; is WAN plugged into a network with internet?"
  fi
fi

echo ">> LAN address -> $LAN_IP"
uci set network.lan.ipaddr=$(q "$LAN_IP")

uci commit
echo ">> rebooting"
( sleep 2; reboot ) >/dev/null 2>&1 &
EOF

cat <<EOF

Done. The Beryl is rebooting. In about a minute:
  LuCI:  http://$LAN_IP  (root / the password you just set)
  SSH:   ssh root@$LAN_IP
  Wi-Fi: "$WIFI_SSID" on 2.4 and 5 GHz, WPA2/WPA3 mixed
Renew your DHCP lease if your machine is still holding a 192.168.1.x address.
EOF
