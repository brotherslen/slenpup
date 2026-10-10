# Handoff: Beryl AX on vanilla OpenWrt

What was done to the GL-MT3000 (Beryl AX) travel router, why each choice was made,
and what's left. `README.md` in this folder is the general runbook. `flash.sh` and
`setup.sh` are scripted versions of the same work and have never run on hardware.

## Current state (as of 2026-09-28)

| | |
|---|---|
| Firmware | OpenWrt 25.12.5 (confirmed in Status > Overview) |
| Admin | LuCI at `http://192.168.8.1`, user `root`, password set |
| LAN | `192.168.8.1/24`, DHCP on |
| WAN | 2.5G port, DHCP client. Plugged into the ASUS while at home |
| 2.4 GHz | SSID `travel_slen_2`, ch 1, 20 MHz, AX, WPA2/WPA3 mixed, country US, `lan` only |
| 5 GHz | Set to ch 36, 80 MHz, same security, country US, `lan` only. Not read back after setup |
| Extra packages | `travelmate`, `luci-app-travelmate`, `luci-proto-wireguard` (confirmed installed) |
| Backup | Config archive saved off the router |
| Tailscale | Not installed, by choice |

## What was done, and why

1. **Factory reset on GL stock 4.7.4.** The unit was used and its password unknown.
   The reset got us into the GL panel to run the flash.

2. **Flashed OpenWrt 25.12.5 from GL's Local Upgrade page, Keep Settings off.**
   - 25.12.5 is the current stable release. 24.10 only gets security fixes, and that
     was ending around Sept 2026.
   - The sha256 was checked before upload.
   - We skipped the offered stock update: 4.7.4 is recent, and the "format error"
     reports come from older 4.x builds. It flashed cleanly.
   - Keep Settings was off so nothing from GL's firmware carried over.

3. **Set it up by hand in LuCI instead of with the scripts.** This Claude session runs
   in a cloud container with no route to the home LAN. The NUC, the first machine
   tried, runs Windows without bash, which the scripts need.

4. **Cabled the 2.5G WAN port to the ASUS, with the setup machine on the 1G LAN
   port.** The Beryl got internet for package installs, and the setup machine reached
   its admin side.
   - The NUC came first, but moving its only Ethernet cable behind the Beryl cut
     Remote Desktop, Roon and Jellyfin. The tower took over.
   - Lesson: do anything on the Beryl's wired side from the tower, never the NUC.

5. **Wi-Fi attached to `lan` only.** `wan` was ticked by mistake on the 2.4 GHz
   interface and was removed. With it ticked, Wi-Fi clients would land on the
   upstream network (home or hotel) instead of behind the Beryl's firewall.

6. **Country code set to US.** On "driver default" the radio falls back to the most
   conservative rules and loses channels and power, mainly on 5 GHz.

7. **5 GHz at 80 MHz on channel 36, not 160 MHz.**
   - In the US, the 160 MHz block overlaps radar channels. The radio would sit
     silent about 60 s after every boot and move whenever it hears radar.
   - [openwrt#16537](https://github.com/openwrt/openwrt/issues/16537) is an open bug:
     5 GHz drops at 160 MHz with more than one network on the radio. That's what
     Travelmate creates when it joins hotel Wi-Fi.
   - 80 MHz (about 1201 Mbps on paper) is far above what hotel uplinks deliver.
   - Channel 36 is legal almost everywhere.

8. **Travel SSID and key, not the home ones.**
   - The key sits in plain text on a device that travels. Reusing the home key
     means a lost Beryl forces a home password change.
   - Copying the home SSID and key exactly would let devices join automatically on
     the road. It only works if the Beryl's Wi-Fi is off at home. Otherwise devices
     can land on the Beryl's separate network and lose sight of Roon, Jellyfin and
     casting.

9. **LAN moved from 192.168.1.1 to 192.168.8.1/24.** Many hotel and ISP gateways
   use 192.168.1.x, and a router whose LAN matches its upstream subnet can't route.
   The address was replaced, not added alongside, and applied with "Apply unchecked"
   so LuCI didn't roll it back.

10. **Installed Travelmate and WireGuard support.**
    - Travelmate joins hotel or airport Wi-Fi as the uplink and detects
      captive-portal sign-in pages.
    - `luci-proto-wireguard` keeps a plain WireGuard tunnel available if ever wanted.

11. **Saved a config backup off the router.** After a reset or reflash, restoring
    it brings all of the above back in one step.

12. **Decided against Tailscale on the Beryl.**
    - The use case was Jellyfin on a laptop. That's better served by the Tailscale
      app on the laptop and the NUC: a laptop CPU encrypts much faster than the
      Beryl's MT7981, and it works without the Beryl.
    - Bandwidth limits regardless of method: every stream leaves the house through
      the home upload. UHD remuxes run 50–80 Mbps, so expect Jellyfin to transcode.
      Set a bitrate cap and turn on Quick Sync on the NUC. Download big titles
      before a trip.
    - Roon on the road should use Roon ARC. Roon's discovery doesn't cross
      Tailscale.
    - Revisit Tailscale on the Beryl only to bring devices that can't run Tailscale
      (Fire TV, Switch) onto the home network, or to route all hotel traffic
      through home. If that happens, the NUC is the exit node; the tower sleeps.

## Next: Travelmate on the road (first use)

Bring the hotel's SSID, the password if there is one, and whether a sign-in page
appears.

The outline below is from memory and hasn't been checked against the 25.12 LuCI
pages:
1. Join the Beryl's Wi-Fi and open `http://192.168.8.1`.
2. Go to Services > Travelmate and enable it.
3. Scan for the hotel network on the matching radio and add it as an uplink with
   its password.
4. If the hotel has a sign-in page, open any `http://` site from a device behind
   the Beryl to get the portal, then accept.

The hotel uplink takes over the radio's channel. Expect the 5 GHz AP to follow
whatever channel the hotel uses.

## Recovery

- **Forgot the password or broke the config:** factory reset with a long press on
  the reset button (over 5 s, standard OpenWrt behaviour, from memory). The Beryl
  comes back at `192.168.1.1` with no password and Wi-Fi off, so it needs a cable.
  Then restore the backup from System > Backup / Flash Firmware.
- **Won't boot:** U-Boot web recovery. Set the PC to static `192.168.1.2/24`, hold
  reset while powering on until the LED blinks blue and goes white, then browse to
  `http://192.168.1.1` and upload the OpenWrt sysupgrade image or GL stock firmware.
  See README section 5.
- **Going back to GL stock:** U-Boot recovery with GL firmware from
  <https://dl.gl-inet.com/router/mt3000>.

## Notes for a future Claude session

- A cloud session can't reach the Beryl. Work happens through the user (LuCI by
  hand), or from a local Claude Code session on the tower via `claude --teleport`.
- `flash.sh` and `setup.sh` prompt for passwords interactively. The user runs them
  in their own Git Bash or Linux terminal, never with passwords pasted into chat.
- What was verified against OpenWrt source at `v25.12.5`: device definition,
  sysupgrade image format accepted by GL stock, WAN=`eth0` (2.5G) and
  LAN=`eth1` (1G), fan driver included, `tailscale` 1.98.3 in the 25.12 feed.
- What was never verified: the openwrt.org wiki page itself and GL's docs (both were
  blocked from the cloud session), and the U-Boot LED sequence.
