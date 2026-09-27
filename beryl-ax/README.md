# GL-MT3000 (Beryl AX): stock to vanilla OpenWrt

Target release: OpenWrt 25.12.5 (current stable as of Sept 2026). 25.12 uses `apk`
in place of `opkg`. 24.10.x also supports this device and gets security fixes until
roughly the end of Sept 2026, so there's no reason to pick it for a fresh install.

Ports, from the 25.12.5 device tree and `02_network`: the 2.5G port is WAN (`eth0`),
the 1G port is LAN (`eth1`).

## 0. Cabling

Plug the Beryl's **2.5G WAN** port into a LAN port on the ASUS. Plug your computer
into the Beryl's **1G LAN** port (or join its stock Wi-Fi; SSID and key are on the
label). If the Beryl's LAN port is the one in the ASUS right now, move it: that puts
two DHCP servers on your home network, and you can't reach the Beryl's admin side
from the ASUS side anyway.

## 1. Factory reset the used unit

With it powered on, hold the reset button about 10 seconds until the LED blinks,
then let go. It reboots to GL defaults at `http://192.168.8.1`. Run the GL setup
wizard so root has a password you know (the wizard's admin password is root's
SSH password).

In the GL panel, check **System > Upgrade**. If it offers a newer stock release,
install it first. Old stock builds can reject OpenWrt images with a format error.

If the seller already put something non-stock on it and the reset doesn't get you
to the GL wizard, use U-Boot recovery (section 5) to load stock first.

## 2. Flash OpenWrt

Either path wipes all settings.

Scripted (Linux, macOS, WSL, Git Bash):

    ./flash.sh

It downloads the sysupgrade image, checks it against the release `sha256sums`,
confirms the board is an MT3000, runs `sysupgrade -T`, then `sysupgrade -n`.

By hand: download
`openwrt-25.12.5-mediatek-filogic-glinet_gl-mt3000-squashfs-sysupgrade.bin` from
<https://firmware-selector.openwrt.org/?target=mediatek/filogic&id=glinet_gl-mt3000>,
check its SHA256, then GL panel > **System > Upgrade > Local Upgrade**, upload it,
and **uncheck Keep Settings**.

Either way, leave power alone for about 3 minutes.

## 3. First-boot setup

OpenWrt comes up at `http://192.168.1.1`, root with no password, Wi-Fi off. Renew
your DHCP lease, then:

    ./setup.sh

It prompts for the root password, SSID and Wi-Fi key, then:

- sets root password and installs your `~/.ssh/id_*.pub` key if one exists
- sets hostname `beryl`, timezone (`ZONENAME` / `TZ_POSIX`, default UTC)
- turns on both radios with one SSID, WPA2/WPA3 mixed, country `US`, 5 GHz at HE80
- installs `travelmate`, `luci-app-travelmate`, `luci-proto-wireguard` (skip with `EXTRAS=0`)
- moves LAN to `192.168.8.1` and reboots

The LAN move keeps it off `192.168.1.x`, which is what most hotel and ISP gateways
hand out; a travel router whose LAN matches its upstream subnet can't route.

Example with a real timezone:

    ZONENAME=America/Chicago TZ_POSIX='CST6CDT,M3.2.0,M11.1.0' ./setup.sh

## 4. After setup

LuCI at `http://192.168.8.1`. Travelmate lives under **Services > Travelmate** and
handles joining hotel Wi-Fi as the uplink, including captive portal detection. The
fan is driven by the kernel `pwm-fan` thermal driver; nothing to configure.

## 5. Recovery (U-Boot web failsafe)

1. Set your computer to static `192.168.1.2/24`, cable in the LAN port.
2. Unplug the Beryl, hold reset, plug power in. Keep holding until the LED blinks
   blue several times (GL says 6 for this model) and goes solid white.
3. Browse to `http://192.168.1.1`, upload a stock GL firmware (from
   <https://dl.gl-inet.com/router/mt3000>) or the OpenWrt sysupgrade image.

To go back to stock from OpenWrt, flash GL's firmware through U-Boot this way.

## Sources

- <https://openwrt.org/toh/gl.inet/gl-mt3000>
- OpenWrt v25.12.5 source: `target/linux/mediatek/image/filogic.mk`,
  `target/linux/mediatek/dts/mt7981b-glinet-gl-mt3000.dts`,
  `target/linux/mediatek/filogic/base-files/etc/board.d/02_network`
- <https://docs.gl-inet.com/router/en/4/faq/debrick/>
