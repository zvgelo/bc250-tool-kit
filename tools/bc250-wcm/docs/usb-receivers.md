# bc250-wcm — USB 2.4 GHz receivers

**English** · [Polski](usb-receivers.pl.md)

## Supported receivers

| Profile | Device | USB IDs | Verified |
|---|---|---|---|
| `8bitdo-ultimate-2c-2g4` | 8BitDo Ultimate 2C Wireless, original 2.4 GHz receiver | `2dc8:310a` pad linked (XInput), `2dc8:301c` no pad (IDLE) or pad linked in DInput mode | on the reference BC-250 (Bazzite); PID meanings agree with the upstream research listed below |

Any other USB device with a joystick input node (or a gamepad driver such as
`xpad`) is listed too, as a plain **USB** controller: from the host a wired pad
and a receiver look the same, so nothing without a profile is called "2.4 GHz".

### The 8BitDo Ultimate 2C receiver

With a pad linked (`2dc8:310a`, bcdDevice 1.14) it has three interfaces:

| Interface | Driver | Function |
|---|---|---|
| 0 | `xpad` | the Xbox 360-compatible gamepad (evdev `eventN`, `uaccess`) |
| 1 | `usbhid` | keyboard / mouse |
| 2 | `usbhid` | vendor-defined HID, usage page `0xFF7A`: input report `0x02` (63 + 1 bytes), output report `0x81` (63 bytes) |

Without a pad (`2dc8:301c`, product string `IDLE`, bcdDevice 2.00) it is a single
vendor-defined HID interface.

**`2dc8:301c` is also DInput mode.** Switched with Home + B (pad firmware 1.06 +
receiver 1.00; newer firmware stays in XInput), the receiver keeps `301c` but
its product string is the controller's name and it exposes a `hid-generic`
gamepad input device — the mode [bc250-paddles](../../bc250-paddles/) needs for
L4/R4. The product ID alone cannot tell IDLE from DInput; the input device can,
so WCM counts `301c` **with** a gamepad input device as a linked pad and `301c`
without one as IDLE.

**The same pad on a USB cable is also `2dc8:310a`** with the same interfaces.
No passive way to tell the two apart has been found (the known one sends a
request). WCM therefore labels a `310a` device *"USB (2.4 GHz receiver or
cable)"* until it has seen that physical device in IDLE (`301c`) — a cable never
goes IDLE. Ports seen that way are remembered in
`~/.local/state/bc250-wcm/receivers`, so the label is right on the next start.

## What can be measured — and what cannot

| Metric | Available | How |
|---|---|---|
| receiver present | yes | sysfs |
| pad linked to the receiver | yes, inferred | product ID `310a` vs `301c`, plus whether `301c` has a gamepad input device (DInput); labelled "from the USB product ID" |
| USB unplug / re-enumeration | yes | sysfs + kernel uevents |
| input activity | yes, if the event node is readable | events/s from evdev, read-only, never grabbed |
| RSSI / signal quality | **no** | not exposed by the receiver; shown as `n/a`, never derived from anything else |
| battery over 2.4 GHz | **no** (stock firmware) | shown as `n/a`; a kernel `power_supply` is used if one ever appears |
| firmware version | not by WCM | only by sending a request on interface 2 — see below |

**No made-up metrics.** The report rate of the vendor interface or the event
rate of the pad is never turned into a "signal" figure. Input activity is shown
as what it is — how much the pad is being used — and a pad lying still is not a
link problem.

### States a receiver panel distinguishes

| What happened | How it shows |
|---|---|
| pad switched off / out of range | receiver goes `301c` without an input device: *NO PAD LINKED*, counted as a drop with reason "receiver has no pad linked" |
| receiver unplugged | *GONE FROM USB*, drop with reason "the device left the USB bus" |
| pad idle, nobody playing | still *pad linked*; input 0/s; after a minute an informational note says this is not a link failure |
| no permission on the event node | link state still works; input shows why it is unavailable |
| receiver moved to another port | the same panel follows it (same profile, unique), logged as "receiver moved" |

Bluetooth-side events (backend timeouts, adapter resets, `hciN` remaps) are
tracked separately and never touch a USB panel.

## Upstream research (checked 2026-10)

What open-source projects actually implement for these devices:

| Project | Targets | Relevance to the 2C receiver |
|---|---|---|
| [philipp-schwarz/8bd-u2cw](https://github.com/philipp-schwarz/8bd-u2cw) | **2C**, `2dc8:310a` | an out-of-tree kernel driver replacing xpad on interface 0 (XInput endpoints); sends an init packet and rumble. No battery, no link data. |
| [mhbxyz/8bitdo-ultimate-linux](https://github.com/mhbxyz/8bitdo-ultimate-linux) | 8BitDo by name match (2C not named) | evdev/udev inspection, SDL mappings; no hidraw protocol. Its udev rule is a `99-…` file, too late for `uaccess`. |
| [s8n/ultimatecontroller-rs](https://github.com/s8n/ultimatecontroller-rs) | original **Ultimate** (`3012`, `6007`, `3011`, `3106`) | configurator; detaches xpad, uses reports `0x81`/`0x02`, config read/write with CRC. Not the 2C, no battery/RSSI. |
| [MiguelSanzBr/8bitdo-ultimate2-linux](https://github.com/MiguelSanzBr/8bitdo-ultimate2-linux) | **Ultimate 2** (`310b`, dongle `6013`) | same report framing; a battery query for the *Ultimate 2* dongle (marked inferred, dongle path untested by the author). Not the 2C. |
| ascendedent/8bitdo-Linux-Software | **2C** | read-only reverse engineering: PID meanings, "an idle read on the vendor interface returned nothing"; probes on interface 2 return the receiver's firmware version. |
| jgus-org/8bitdo-2c-re-flake | **2C** firmware | "no charge/battery telemetry over 2.4 GHz or wired" on stock firmware; interface 2 is the firmware-flash channel; some commands rewrite the radio address and wipe the pairing. |

Also: mainline `xpad.c` lists `2dc8:310a` (XTYPE_XBOX360) and has no battery
code; SDL's 8BitDo driver does not handle `310a` and documents no battery byte for
the 2C. Steam's `60-steam-input.rules` grants `uaccess` to every `2dc8` hidraw
node.

**Conclusion.** Nothing reports RSSI for the 2C over 2.4 GHz, and battery needs
patched firmware. Reading the receiver firmware version is possible only by
writing a request to the same interface that flashes firmware; WCM does not do
it. If a read-only query is ever confirmed safe on the 2C by a reliable source,
it belongs in a separate, explicitly invoked command — never in passive
monitoring.

## Adding another receiver

1. Collect facts on the real device, without writing anything:
   ```bash
   bc250-wcm --list
   bc250-wcm hid list
   bc250-wcm hid descriptor <vid:pid:interface>
   udevadm info -a -n /dev/hidrawN | head -40
   ```
   Note the product IDs in every state (pad on, pad off, pairing), the interfaces
   and their drivers, and whether the pad also exists as a cable device.
2. Add a `ReceiverProfile` to `PROFILES` in `bc250wcm/usb.py`: `vendor`, `modes`
   (`{pid: "active" | "idle"}`), `name`, `verified` (who saw what, when),
   `shared_with_cable` for product IDs the wired pad uses as well.
3. Model it in `bc250wcm/fakesys.py` (both modes) and add tests in
   `tests/test_wcm.py`: detection, the mode switch keeping the identity, removal.
4. Only metrics that the device provides without requests go into the panel.

## Researching an unknown HID interface safely

The `hid` subcommand exists for this; it reads and never writes.

1. **Descriptor first.** `bc250-wcm hid descriptor 2dc8:310a:2` gives report IDs,
   directions and sizes. It never says what bytes mean.
2. **Baseline.** `bc250-wcm hid capture 2dc8:310a:2 -t 30 -o idle.jsonl` with the
   pad untouched. Many vendor interfaces send nothing unsolicited — the 2C's
   interface 2 is reported to stay silent — and that is a result too.
3. **Marked actions.** `bc250-wcm hid capture … --marks -o actions.jsonl`, then
   type a label and Enter at the moment of each action ("A pressed", "pad off",
   "pad on").
4. **Analyse.** `bc250-wcm hid analyze actions.jsonl idle.jsonl`: constant vs
   changing bytes, value ranges, offsets that differ between captures, and the
   bytes that changed right after each mark. A report of the wrong length is
   counted and skipped, not interpreted.
5. **Repeat** each action several times before believing a byte means something.
6. **Never** send output or feature reports to a receiver to "see what happens".
   On the 8BitDo receiver that interface also updates firmware; documented
   commands exist that change the radio address and erase the pairing.

The reading side needs read access to the hidraw node: Steam's rule usually
grants it; otherwise `install.sh --udev` adds a `uaccess` rule for this receiver
only ([permissions](permissions.md)).
