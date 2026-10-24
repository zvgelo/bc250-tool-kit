# How bc250-paddles works

**English** · [Polski](how-it-works.pl.md)

All values below were measured on a real pad and receiver: a Fedora 44 machine
(kernel 7.2, Flatpak Steam), cross-checked against Steam's own
`logs/controller.txt`. The `environment.d` path was then confirmed on a BC-250
with Bazzite in Gaming Mode: Steam had the variable and logged the mapping with paddles.

## The receiver's three faces

The 2.4 GHz receiver changes its USB product ID depending on what is linked to
it and in which mode:

| USB ID | Input device | Meaning | L4/R4 |
|---|---|---|---|
| `2dc8:301c`, product `IDLE` | none (vendor HID only, page `0xFFA0`) | receiver with no pad linked | — |
| `2dc8:310a` | `xpad`, 11 buttons | pad linked in **XInput** mode | **not sent** |
| `2dc8:301c`, product `8BitDo Ultimate 2C Wireless Controller` | `hid-generic`, 15 buttons | pad linked in **DInput** mode | `BTN_C` / `BTN_Z` |
| `2dc8:301b` (Bluetooth) | `hid-generic`, 16 buttons | pad over Bluetooth | `BTN_C` / `BTN_Z` |

`301c` alone does not tell IDLE from DInput. The input device does: IDLE has
none, DInput has an `eventN` with `Product=301c` in `/proc/bus/input/devices`.
`bc250-paddles status` reads exactly that.

### XInput: nothing to map

In XInput mode the receiver sends the fixed 20-byte Xbox 360 report. A `usbmon`
capture while pressing L4, R4, L4+A and R4+A shows packets only for A. L4/R4
produce **no USB packet at all**, so no driver, SDL mapping or `uinput` trick
can recover them.

### DInput: L4/R4 are real buttons

The DInput descriptor (145 bytes) declares 15 buttons, a hat, four 8-bit axes
and analog Accel/Brake. Measured codes and SDL indices (SDL numbers evdev keys
in ascending code order):

| Button | evdev | SDL |
|---|---|---|
| A, B, X, Y | 304, 305, 307, 308 | b0, b1, b3, b4 |
| **L4** | **306 `BTN_C`** | **b2 → `paddle2`** |
| **R4** | **309 `BTN_Z`** | **b5 → `paddle1`** |
| LB, RB | 310, 311 | b6, b7 |
| LT, RT | `ABS_BRAKE`, `ABS_GAS` (+ 312, 313 digital) | a5, a4 |
| Select, Start, Home | 314, 315, 316 | b10, b11, b12 |
| L3, R3 | 317, 318 | b13, b14 |

This is the same numbering as over Bluetooth, where SDL's own database already
carries `paddle1:b5,paddle2:b2`.

### Firmware

Switching to DInput (Home + B) works with **pad 1.06 + receiver 1.00**. With
**pad 1.09 + receiver 1.03** the receiver comes back as `310a` every time.
The same is reported in SDL issue #15658 and on the Steam Community forum
(August 2026).

## Why config.vdf is not enough

Steam stores SDL mappings in `config/config.vdf` under `SDL_GamepadBind`, and
the usual advice is to add `paddle1:b5,paddle2:b2` there. For this device it
does not work. Steam's controller log shows what happens:

| Attempt | `SDL Mapping for 2dc8/301c` in the log | Paddles |
|---|---|---|
| config.vdf entry without CRC | automatic mapping, no paddles; entry overwritten on exit | no |
| config.vdf entry with `crc:6fed` | automatic mapping, no paddles; entry overwritten on exit | no |
| `SDL_GAMECONTROLLERCONFIG` in Steam's environment | **our mapping** | **yes** (L5/R5) |
| config.vdf only, variable removed again | automatic mapping, no paddles | no |

Steam builds SDL's automatic mapping from the evdev codes, which has no slot for
`BTN_C`/`BTN_Z`. It then writes that mapping back into `config.vdf` when it
exits, which is why hand edits "do not survive a restart". A mapping from
`SDL_GAMECONTROLLERCONFIG` is loaded with a higher priority and is used.

`crc:6fed` matters: Steam's GUID for the device is `0300ed6f…` (the CRC of the
device name in bytes 2–3). A mapping without the CRC loses to a CRC-matched one.

## Where the variable goes

| Steam | Mechanism | Picked up |
|---|---|---|
| native | `~/.config/environment.d/90-bc250-paddles.conf` | by the systemd user manager at login, and by sessions that source `environment.d` |
| native, right away | `systemctl --user set-environment` | by user services started after the call |
| Flatpak | `[Environment]` in `~/.local/share/flatpak/overrides/com.valvesoftware.Steam` | every Flatpak Steam start |

The `environment.d` file uses `KEY="value"`, which is valid both for
systemd and for a shell that sources the file, as gamescope sessions do.

**Verify, do not assume.** Whether a given session passes the variable to Steam
shows in `bc250-paddles status`. It looks at two things:

1. **The running Steam's environment** (`/proc/<pid>/environ`). A sandboxed
   process may hide it, and then the tool says so instead of guessing.
2. **Steam's log** (`logs/controller.txt`). The last `SDL Mapping for
   2dc8/301c` line says which mapping was used. That is the final word.

## Rumble in DInput

There is none, at two levels:

- **Kernel:** the device is driven by `hid-generic`, which implements no force
  feedback. `event` has no `EV_FF`, so games and `fftest` have nothing to talk
  to. The descriptor has no Physical Interface Device (`0x0F`) page, which the
  generic HID force-feedback path would need.
- **Firmware:** the descriptor does declare a vendor-style output (report
  `0x01`, LED page usages `0x43`–`0x46`, 8 bytes). Writing it directly through
  `hidraw` (all bytes, then each pair) produced no vibration. The firmware does
  not drive the motors in this mode, as the SDL #15658 reports also say.

A driver could not fix this, because the pad ignores the request.

## Undoing by hand

```bash
rm ~/.config/environment.d/90-bc250-paddles.conf
systemctl --user unset-environment SDL_GAMECONTROLLERCONFIG
flatpak override --user --show com.valvesoftware.Steam   # then delete the SDL_GAMECONTROLLERCONFIG= line
```

`bc250-paddles off` does exactly this and leaves other overrides alone.
