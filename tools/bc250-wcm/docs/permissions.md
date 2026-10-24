# bc250-wcm — permissions

**English** · [Polski](permissions.pl.md)

## What needs privileges, and what does not

| Data | Interface | Privileges |
|---|---|---|
| adapters, devices, state, battery, UUIDs, GATT | BlueZ on the system D-Bus | none (BlueZ's D-Bus policy allows reads to everyone) |
| starting / stopping discovery | `Adapter1.StartDiscovery` | none on a default Fedora/Bazzite policy |
| USB ID and port of an adapter | sysfs | none |
| kernel Bluetooth messages | `journalctl -k` | membership in `wheel`, `adm` or `systemd-journal` |
| **RSSI and TX power of a live link** | mgmt *Get Connection Information* | **CAP_NET_ADMIN** |
| USB controllers and receivers, their state | sysfs, kernel uevents | none |
| input activity of a pad | `/dev/input/eventN`, read-only | none for gamepads: systemd tags joystick nodes with `uaccess` |
| `hid capture` on a receiver | `/dev/hidrawN`, read-only | read access — Steam's rule, or `install.sh --udev` |

The kernel lets an unprivileged socket on the mgmt control channel send only the
"untrusted" read commands (version, index list, controller info). *Get
Connection Information* and *Get Connections* are refused with status 0x14,
*Permission Denied* — the same thing `btmgmt conn-info` reports without sudo.

## The design: a small helper, not a root interface

Running the whole curses interface as root would be the simple way, and the
wrong one. Instead `bc250wcm/mgmt.py` doubles as a stand-alone helper:

- it is started **once** per monitor run, through sudo;
- it speaks JSON lines on stdin/stdout;
- it accepts exactly four operations — `ping`, `index_list`, `connections`,
  `conn_info` — all read-only mgmt commands;
- every argument is checked before use: the controller index is an integer
  0–65534 (`bool` refused), the address must match `^[0-9A-F]{2}(:[0-9A-F]{2}){5}$`,
  the address type is 0, 1 or 2, a request line is at most 4 KiB, at most 16
  queries are in flight;
- it takes no command-line arguments (it refuses any), imports nothing outside
  the standard library and nothing from its own directory (`python3 -I`);
- it runs no programs and opens no files; its only resource is the mgmt socket;
- it exits when its stdin closes — i.e. when the monitor exits or dies, so it
  never lingers.

It cannot power an adapter off, unpair, connect, disconnect or reset anything.

## Option 1: one sudo prompt per start (default)

Without any setup, `bc250-wcm` explains why it needs sudo and runs
`sudo -v` **before** curses takes over the terminal, then starts
`sudo -n -- /usr/bin/python3 -I …/bc250wcm/mgmt.py`. Ctrl-C at the prompt
skips it: the monitor runs without link RSSI and says so in the Diagnostics strip.

A recent `sudo` in the same terminal counts — then nothing is asked at all.

`--no-sudo` (or `sudo = never` in the configuration) never asks.

## Option 2: no prompt — `install.sh --rssi-helper`

```bash
./tools/bc250-wcm/install.sh --rssi-helper
```

This (asking for your password once):

1. copies `mgmt.py` to `/usr/local/libexec/bc250-wcm/rssi-helper`,
   owner root, mode 0755 — `/usr/local` is writable and persistent on Bazzite;
2. writes `/etc/sudoers.d/bc250-wcm`, validated with `visudo -cf` first:

   ```
   <you> ALL=(root) NOPASSWD: /usr/local/libexec/bc250-wcm/rssi-helper ""
   ```

   `""` means *no arguments allowed*. The rule names one user and one file.

Why a copy and not a rule pointing into the repository: a NOPASSWD rule for a
file the user can edit is a root shell for anyone who gets the user's shell.
The installed copy is root-owned in a root-owned directory; the monitor uses it
only after checking exactly that (owner root, not group- or world-writable,
same for the directory).

After `git pull`, run `install.sh --rssi-helper` again to update the copy (the
monitor refuses a helper with a different protocol version and falls back to the
prompt).

Remove: `./tools/bc250-wcm/install.sh --remove-rssi-helper`.

## Option 3: btmgmt

`--rssi-backend btmgmt` runs `sudo -n -- btmgmt -i N conn-info -t T ADDR` for
every sample: an argument list (no shell), validated arguments, a hard timeout,
colour codes stripped. It needs a cached sudo timestamp or your own sudoers rule
for `btmgmt`, and it spawns a process per device per second — a fallback, not the
default. A rule for all of `btmgmt` is broader than the helper rule (btmgmt can
also power adapters off and unpair), which is why the kit does not install one.

## USB receivers and `hid capture`

Monitoring a receiver needs no privileges at all: everything comes from sysfs,
and the pad's event node is readable by the user at the seat (`uaccess` via
systemd's `70-uaccess.rules` for `ID_INPUT_JOYSTICK`). The node is opened
read-only and never grabbed (no `EVIOCGRAB`), so games, Steam and `xpad` keep
every event; no interface is ever unbound from its driver.

Only `bc250-wcm hid capture` opens a hidraw node, again read-only. Fedora creates
hidraw nodes as `root:root 0600`, but Steam's `60-steam-input.rules` (installed
with Steam on Bazzite) adds `TAG+="uaccess"` for every `2dc8` hidraw node. If it
is missing, `install.sh --udev` installs `/etc/udev/rules.d/70-bc250-wcm.rules`:

```
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="2dc8", ATTRS{idProduct}=="310a|301c", TAG+="uaccess"
```

`uaccess` gives an ACL to the user logged in at the seat and to nobody else — no
`MODE="0666"`, no `chmod 666 /dev/hidraw*`. The file must sort before
`73-seat-late.rules`, which applies the tag; a `99-…` rule would have no effect.
Remove it with `install.sh --remove-udev`.

## Option 4: root

Run as root, the monitor opens the mgmt socket in-process (`direct`). It works,
and the Diagnostics strip reminds you that the helper would have been enough.
