# Handoff: Beryl AX to vanilla OpenWrt

For whoever picks this up next, whether that's a Claude Code session on the tower or
a person at the keyboard. Read `README.md` in this folder for the full runbook; this
file covers where things stand and what the next session needs to know.

## Where things stand (2026-09-27)

- A used GL-MT3000 (Beryl AX) is on the bench, cabled somewhere into the home network
  behind an ASUS RT-AX88U Pro. Its current firmware and password state are unknown.
- `flash.sh`, `setup.sh` and `README.md` are written and pushed on branch
  `claude/beryl-ax-openwrt-setup-r1gqr7`. **Neither script has run against real
  hardware.** The router-side part of `setup.sh` passed a dry run against a
  stubbed `uci`.
- Nothing on the router has been reset, flashed or configured yet.
- The first session ran in a cloud container with no LAN access, which is why this
  is handed off.

## What was verified and what wasn't

Checked against OpenWrt source at tag `v25.12.5` on GitHub:
- 25.12.5 is the current stable release; 25.12 uses `apk`, not `opkg`.
- Device definition `glinet_gl-mt3000` in `target/linux/mediatek/image/filogic.mk`.
  The sysupgrade image gets `append-gl-metadata`, which is what lets GL stock
  firmware accept it.
- Ports: WAN is `eth0` (2.5G), LAN is `eth1` (1G), per `02_network` and the DTS.
- Default packages include `kmod-hwmon-pwmfan`, so the fan needs no setup.

Not verified (openwrt.org and GL.iNet sites were blocked from the cloud session):
- The image filename pattern and download URL in `flash.sh`. If the name is wrong,
  the `sha256sums` lookup fails and the script stops before touching the router.
- The U-Boot recovery LED sequence (from search snippets: blue blinks about 6
  times, then solid white).
- Whether GL stock `sysupgrade -T` accepts the image on the unit's current stock
  version. Old stock builds reportedly reject it; updating stock first fixes that.
- The OpenWrt wiki page's own install notes for this model. Read it before flashing:
  <https://openwrt.org/toh/gl.inet/gl-mt3000>

## Cabling for a tower-driven flash

The tower needs to be on the Beryl's LAN side, and it needs internet to download the
image and keep Claude Code talking to the API. Simplest layout:

    ASUS LAN port ── Beryl 2.5G (WAN)
    Beryl 1G (LAN) ── tower Ethernet

The tower's internet then runs through the Beryl. It drops for about 3 minutes
during the flash and about 1 minute during the setup reboot. A Claude Code session
will show API errors in those windows; wait it out, don't retry commands.

Alternative: the tower's motherboard (MSI B660M Mortar WiFi) has Wi-Fi, so it can
join the Beryl's stock SSID for the flash while Ethernet stays on the ASUS. That
breaks right after the flash, because fresh OpenWrt boots with Wi-Fi off, so a cable
is needed for `setup.sh` anyway. Use the cable from the start.

## Rules for the next Claude session

1. **Don't flash without the user's go-ahead in that session.** Show the board name
   and the checksum result first. A bad flash on this unit means U-Boot recovery.
2. **Interactive prompts don't work in Claude Code's Bash tool.** `flash.sh` asks for
   the GL admin password over SSH, and `setup.sh` prompts for the root password and
   Wi-Fi key. Have the user run both scripts in their own Git Bash (or Linux) terminal.
   Claude handles preflight and verification. Don't ask the user to paste passwords
   into the chat.
3. **Give Claude non-interactive SSH after setup.** `setup.sh` installs the first
   `~/.ssh/id_ed25519.pub` / `id_ecdsa.pub` / `id_rsa.pub` it finds. If the tower
   has no key, create one before `setup.sh` runs (`ssh-keygen -t ed25519`, with the
   user's OK). After that, Claude can verify with
   `ssh -o BatchMode=yes root@192.168.8.1 ...`.
4. **Watch for Windows quirks.** The scripts need bash: use Git Bash, which Claude
   Code on native Windows uses for its Bash tool when Git for Windows is installed.
   To renew DHCP from Git Bash, run `ipconfig //release` then `ipconfig //renew`;
   the doubled slash stops MSYS path mangling. Or run it from PowerShell.
5. **Don't unplug power** while the router is flashing.

## Steps

1. Detect the tower's OS and shell. Confirm `bash`, `ssh`, `curl` and `sha256sum`
   exist. Check the cabling above with the user.
2. User: hold reset about 10 s, then run the GL wizard at `http://192.168.8.1` and
   set an admin password. Update GL stock firmware if the panel offers an update.
3. Claude (preflight, no password needed): `curl -s http://192.168.8.1 >/dev/null`
   answers. Optionally download the image and check it by running the first half
   of `flash.sh` by hand.
4. User runs `./flash.sh`. After it finishes, wait about 3 minutes, then renew DHCP.
   The PC should get a 192.168.1.x address.
5. User runs `./setup.sh`, answering the prompts. Decide the settings first
   (see below).
6. After the reboot, renew DHCP again (the PC should get 192.168.8.x). Claude verifies:
   - `ssh -o BatchMode=yes root@192.168.8.1 'ubus call system board'` shows
     release 25.12.5 and model GL-MT3000
   - `wifi status` shows both radios up with the chosen SSID
   - `apk list --installed | grep -E 'travelmate|wireguard'`
   - `ping -c3 openwrt.org` from the router (WAN works)
   - LuCI loads at `http://192.168.8.1`
7. Commit anything that changed (script fixes, notes) to this branch.

## Decisions for the user before `setup.sh`

- `COUNTRY` for Wi-Fi regulatory (defaults to `US`)
- `ZONENAME` and `TZ_POSIX` (defaults to UTC). Example for US Central:
  `ZONENAME=America/Chicago TZ_POSIX='CST6CDT,M3.2.0,M11.1.0'`
- SSID and Wi-Fi key (prompted)
- `LAN_IP` (defaults to `192.168.8.1`, which keeps it off the 192.168.1.x subnets
  common on hotel and ISP gateways)
- `EXTRAS=0` to skip installing travelmate and WireGuard

## If something goes wrong

- `flash.sh` says stock rejected the image: update GL stock firmware, then rerun.
- The router doesn't answer at 192.168.1.1 five minutes after the flash: power-cycle
  it once, then try U-Boot recovery (README section 5) with the OpenWrt image or GL
  stock firmware.
- Setup fails partway: UCI changes aren't saved until the final `uci commit`, but
  the root password and SSH key take effect immediately. Rerun `setup.sh` against
  192.168.1.1; it will ask for the new root password unless the key got installed.
  If it got as far as the reboot, rerun with `ROUTER=192.168.8.1`.
