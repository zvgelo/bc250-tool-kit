# bc250-wcm (Wireless Controller Monitor) — architecture and data sources

**English** · [Polski](architecture.pl.md)

## Modules

```
bc250wcm/
  dbus.py         minimal D-Bus client on the wire protocol (EXTERNAL auth, calls, signals)
  bluez.py        BlueZ object cache: GetManagedObjects + InterfacesAdded/Removed + PropertiesChanged
  model.py        AdapterInfo / DeviceInfo from object-manager dicts, adapter identity, sysfs
  mgmt.py         kernel Management API: encoding, client — and the privileged helper itself
  telemetry.py    link-RSSI backends: helper, direct, btmgmt, none
  engine.py       adapter lifecycle, snapshot diffs -> events, per-device probe threads
  history.py      samples, Welford statistics, connection accounting, event log
  kernlog.py      journalctl -k follower and classifier (timeouts, resets, firmware)
  diagnostics.py  findings for the Diagnostics strip
  export.py       CSV / JSON export, live CSV recorder
  config.py       INI configuration under $XDG_CONFIG_HOME
  usb.py          USB backend: sysfs scan, receiver profiles, physical identity, uevent source
  inputs.py       input activity: counting evdev events (read-only, never grabbed)
  hid.py          HID descriptors, hidraw enumeration, passive capture and analysis
  hidcli.py       `bc250-wcm hid …`
  fakesys.py      a miniature sysfs tree with USB controllers (demo and tests)
  tui.py          curses: device list, dashboard, details, log, help
  widgets.py      palette (256 / 8 / none), boxes, bars, line graphs with gaps
  cli.py          options, backend selection (sudo), --list / --json / --headless
  demo.py         a simulated stack that produces real BlueZ-shaped data
  i18n.py         English and Polish texts
```

Responsibilities as asked for: Bluetooth backend (`dbus`, `bluez`, `mgmt`),
adapter discovery and lifecycle (`model`, `engine`), device discovery (`bluez`,
`model`), connection telemetry (`telemetry`, `mgmt`), sampling and history
(`engine`, `history`), statistics (`history`), TUI (`tui`, `widgets`),
configuration (`config`), export (`export`), diagnostics (`diagnostics`,
`kernlog`).

## Backends and the common model

The interface never talks to a backend; it reads the engine. The engine holds
one **Track** per monitored controller, whatever carries it:

| | Bluetooth backend | USB backend |
|---|---|---|
| discovery | BlueZ object cache (`bluez.py`) | sysfs scan + kernel uevents (`usb.py`) |
| identity | adapter identity + device address | physical place on the bus (`usb:3-5`), or a real serial |
| key of a Track | `<adapter identity>/<address>` | `usb:<physical id>` |
| connected | `Device1.Connected` | receiver mode from the product ID (active / idle) |
| samples | `ok` / `no-rssi` / `timeout` / `disconnected` / `absent` / `no-adapter` | `linked` / `link-idle` / `usb-gone` |
| RSSI | mgmt *Get Connection Information* | never (`rssi_capable = False`) |
| activity | evdev node found by `uniq` = address | evdev node of the joystick interface |

A **Target** (`engine.py`) is what the device list hands to the engine: key,
name, kind, address and, for USB, the receiver profile. Each Track has its own
probe thread, so a slow Bluetooth query never delays a USB panel or the other
way round. Samples share one shape (`history.Sample`): `rssi`, `activity`,
`battery`, `usb_id` — absent values are `None`, never 0.

A third technology would add a source (discover, identify, report state), its
sample states and a panel; the selection, history, export and event log are
shared.

## Why no dbus-python, no Textual

The kit runs on atomic images and uses only the standard library, so that
nothing has to be layered with `rpm-ostree` or built with `pip`. The D-Bus
subset BlueZ needs — authenticate, call, subscribe to signals — is about 400
lines on top of `socket`. The interface uses `curses`, like `bc250-mon`, so both
tools look and behave alike.

## Threads

| Thread | What it does |
|---|---|
| `dbus-reader` | reads the bus socket, completes calls, applies BlueZ signals to the cache |
| `bluez` | connects / reconnects, full resync at start, when bluetoothd reappears, and every 30 s |
| `engine` | on every cache change: diff the snapshot, emit events, update connection state |
| `probe-…` (one per monitored device) | takes one sample per interval; a query that hangs until its timeout delays only that device |
| `helper-out`, `helper-err` | read the helper's replies and events |
| `kernlog` | reads `journalctl -k -f -o json` |
| main | curses: redraws when the engine signals new data, on a key, and twice a second |

The helper answers each query on its own thread too, so four pads are four
independent queries all the way down to the kernel.

## Link RSSI: why not Device1.RSSI

`org.bluez.Device1.RSSI` comes from **advertising reports** and BlueZ updates it
only during discovery. A gamepad that is already connected does not advertise,
and discovery is exactly what should not be running next to it (see the 0x2042
note below). So the monitor asks the kernel for the RSSI of the **link** with the
Management API command *Get Connection Information* (0x0031): the kernel issues
`HCI_Read_RSSI` and, the first time, `HCI_Read_Transmit_Power_Level` on the
connection handle.

What the reply means:

- **LE**: RSSI in dBm.
- **BR/EDR**: `HCI_Read_RSSI` returns the difference from the controller's
  *golden receive power range* in dB — 0 means "inside the range". It is not an
  absolute level, and the monitor labels it `dB (rel.)`.
- **127** is HCI's "no value" (`HCI_RSSI_INVALID`, `HCI_TX_POWER_INVALID`). It is
  shown as *n/a*, never as a number.
- **TX power** is what the controller reports for the link. A value of 0 can be
  a real 0 dBm or a controller that fills the field with zero; there is no way
  to tell from the host, so the interface says *reported, not verified*.
- The kernel **caches** the answer for a random 1–3 s
  (`conn_info_min_age` / `conn_info_max_age` in debugfs). At a 1 s interval some
  consecutive samples are the same reading; that is expected.

Advertising RSSI is still used, clearly labelled, when there is no link-RSSI
backend and the adapter happens to be discovering.

## Address types

mgmt needs the address type: 0 BR/EDR, 1 LE public, 2 LE random. BlueZ's
`AddressType` says only `public` or `random`, so:

1. a type that already worked for this connection is reused;
2. BlueZ 5.80+ exposes `org.bluez.Bearer.LE1` / `Bearer.BREDR1` with their own
   `Connected` — that settles it;
3. `random` is LE by definition (the 8BitDo pads);
4. otherwise both are tried, BR/EDR first for a device with a Class, LE public
   first for one with an Appearance. "Not Connected" moves on to the next type.

The remembered type is dropped on disconnect — the next link may use the other
bearer.

## Adapter identity and the hci0 → hci1 remap

An adapter is identified by its controller address, not by `hciN`. When two
present adapters share an address (cheap Realtek dongles are known to), the
physical USB port from `/sys/class/bluetooth/hciN/device` is appended:
`00:E0:4C:01:02:03@1-4`.

A monitored device is keyed by *adapter identity + device address*. On every
sample the engine resolves the adapter again: same identity → same address if
unique → same USB vendor:product on the same port (an adapter that came back
with another address). So when `hci0` disappears and `hci1` appears with the
same controller, the device panel simply continues: the gap is recorded as
`no-adapter` / `disconnected` samples, the log says
`Adapter … re-registered: hci0 → hci1`, and the counters show one drop and one
reconnect.

Nothing resets the adapter or restarts a service. Those remain the user's
decision.

## Kernel events

`journalctl -k -f -o json -n 400` provides the current boot's backlog (marked
*earlier this boot*) and then live lines. Recognised:

| Line | Meaning |
|---|---|
| `hciN: command 0xXXXX tx timeout` | the controller did not answer a command; opcode named (0x2042 = LE Set Extended Scan Enable) |
| `hciN: Opcode 0xXXXX failed: -110` | the command failed; errno named (-110 = ETIMEDOUT) |
| `hciN: Resetting usb device` | btusb resets the dongle; expect the adapter to disappear |
| `hciN: Hardware error 0x..` | controller-reported hardware error |
| `hciN: RTL: …`, firmware lines | firmware loading |
| `usb X-Y: USB disconnect` | only when X-Y is the port of a known adapter |

Counters per `hciN` feed the Diagnostics strip; timeouts on scan opcodes add a
hint to keep discovery off. Readable without root for members of `wheel`,
`adm` or `systemd-journal` (the default user on Bazzite is in `wheel`).

## Samples and statistics

Each probe tick yields one sample with a state:

| State | Meaning | RSSI |
|---|---|---|
| `ok` | connected, RSSI read | value |
| `no-rssi` | connected, no value (no backend, refused, 127) | none |
| `timeout` | connected, the query timed out | none |
| `disconnected` | known to BlueZ, not connected | none |
| `absent` | BlueZ no longer knows the device | none |
| `no-adapter` | the device's adapter is gone | none |

Min / max / mean / standard deviation (Welford) use only real readings.
*RSSI availability* = samples with a value ÷ samples taken while connected.
*Connected share* = connected samples ÷ all samples. Drops are counted on a
transition from connected to not connected, reconnects on the way back after a
drop, downtime between the two.

The graph's x axis is **time**: bins of one interval ending now. A bin without a
sample — a pause, a stalled query — is empty, and a bin in which the link was
down gets a red marker on the baseline.
