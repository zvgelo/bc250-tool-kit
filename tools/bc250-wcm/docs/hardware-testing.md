# bc250-wcm — testing on real pads

**English** · [Polski](hardware-testing.pl.md)

The automated tests mock BlueZ, the mgmt socket and `btmgmt`. This checklist is
the part only real hardware can answer: two 8BitDo Ultimate 2C pads on the
MERCUSYS MA530 (RTL8761BU) of the BC-250. Nothing here resets the adapter or
restarts a service unless a step says so explicitly and you choose to do it.

Note the results (OK / not OK, plus anything odd) per step; the event log
(`~/.local/state/bc250-wcm/events.jsonl`) and a JSON export (`j`) are the most
useful things to attach to a report.

## 0. Preparation

```bash
cd ~/bc250-tool-kit && git pull
./run.sh bc250-wcm --version
python3 -m unittest discover -s tools/bc250-wcm/tests      # must say OK
```

Both pads paired and connected (they show up in Steam / `evtest`).

## 1. Discovery without privileges

```bash
./run.sh bc250-wcm --list
```

Expect: the MA530 under its current `hciN` with `usb=2c4e:0115` and a port
(e.g. `1-4`); both pads as `gamepad LE`, `Conn yes`, `Pair yes`. Note their
addresses — below they are `$PAD1` and `$PAD2`:

```bash
PAD1=E4:17:D8:xx:xx:xx; PAD2=E4:17:D8:yy:yy:yy
```

## 2. Link RSSI against btmgmt

```bash
sudo btmgmt -i <N> conn-info -t 2 $PAD1        # N from step 1
./run.sh bc250-wcm --headless 15 -m $PAD1 $PAD2
```

Accept the sudo prompt. Expect one line per pad per second, state `ok`, RSSI in
the same range as `btmgmt` printed (±5 dB — the radio moves). The summary at the
end shows min / max / avg and `drops 0`.

## 3. The interface

```bash
./run.sh bc250-wcm
```

1. The picker lists both pads; select them with `1` and `2` (or Space), Enter.
2. Two panels side by side; each title carries the last two bytes of its MAC
   (the names are identical). The RSSI line updates every second.
3. Move one pad to the far side of the room or behind your body: its RSSI drops
   and the graph follows; the other stays.
4. Enter on a panel: details. Check: address type `random`, bearer `LE`,
   connection type `LE Random`, TX power as *reported*, battery (*not exposed*
   is a valid result — say which), the UUID list, GATT services.
5. `+` / `-` change the interval; `p` pauses (graph freezes, header says PAUSED).
6. Resize the terminal down to ~80 × 24: panels go compact, nothing overlaps.

## 4. Disconnect and reconnect

1. Switch PAD2 off. Within a few seconds its panel turns *DISCONNECTED*, the
   graph gets a gap with a red marker, `drops 1`. PAD1 keeps sampling — its
   panel must not stall.
2. Switch PAD2 on. It reconnects on its own; `reconn 1`, the down time shown, the
   graph continues after the gap. The event log (`l`) has both events, with the
   kernel's reason (e.g. *terminated by remote host*).

## 5. The adapter disappearing (USB re-plug)

This is the hci0 → hci1 situation, provoked by hand. Unplug the MA530, wait 5 s,
plug it back into **the same port**.

Expect, without restarting the monitor:

- both panels: *ADAPTER GONE*, then *DISCONNECTED*, then *connected* again once
  the pads reconnect;
- the header shows the new `hciN` (often a higher number);
- the event log: *Adapter … disappeared*, *Adapter … re-registered: hci0 → hci1*
  (or *came back* if the index stayed), the pads' disconnects and connects;
- Diagnostics: the remap and, if the kernel logged them, the USB disconnect.

Optionally re-plug into **another** port: the adapter is still recognised (same
controller address); `--list` shows the new port.

## 6. The 0x2042 timeout (only if it happens)

The original failure cannot be triggered on demand. If it recurs while the
monitor runs, expect in the event log:

```
kernel  Bluetooth: hci0: command 0x2042 tx timeout  [LE Set Extended Scan Enable]
kernel  Bluetooth: hci0: Opcode 0x2042 failed: -110  [LE Set Extended Scan Enable]  [ETIMEDOUT]
kernel  Bluetooth: hci0: Resetting usb device.
adapter Adapter hci0 … disappeared
adapter Adapter … re-registered: hci0 → hci1
```

and in Diagnostics the counters plus the advice to keep discovery off. If it
happened earlier in the current boot, the same lines appear at start-up marked
*earlier this boot*.

The `d` key starts discovery — which is exactly what 0x2042 does. Use it on the
BC-250 only if you want to try reproducing the problem.

## 7. Without privileges

```bash
./run.sh bc250-wcm --no-sudo
```

Expect: state, battery and events work; the RSSI line says why there is no
value; Diagnostics: *No link RSSI: needs privileges …*. Nothing crashes.

## 8. The helper installed

```bash
./tools/bc250-wcm/install.sh --rssi-helper
sudo -n /usr/local/libexec/bc250-wcm/rssi-helper </dev/null   # one "ready" line, exits
./run.sh bc250-wcm                                            # no password prompt
```

Header: `RSSI: mgmt helper`. After quitting with `q`:

```bash
pgrep -af rssi-helper || echo "helper gone"     # must print: helper gone
```

## 9. The 2.4 GHz receiver (8BitDo Ultimate 2C)

1. Plug in the receiver with the pad **off**. `./run.sh bc250-wcm --list`: under
   *USB controllers and 2.4 GHz receivers* one entry, `2dc8:301c`, *no pad linked
   (IDLE)*, with its `usb:<port>` id.
2. Switch the pad on (2.4 GHz mode). `--list` again: the **same** `usb:<port>`
   id, now `2dc8:310a`, *pad linked*, labelled *USB 2.4 GHz receiver* (it was seen
   IDLE on that port). Nothing new appears as a second controller.
3. `./run.sh bc250-wcm`, select the receiver together with a Bluetooth pad.
   Receiver panel: *pad linked*, `RSSI: n/a — not reported by the receiver`,
   Battery `n/a`, an input-activity graph that moves while you play and drops to
   0 when you put the pad down — the panel stays *pad linked*.
4. Switch the pad off: the panel goes *NO PAD LINKED* within ~2 s, `drops 1`,
   the event feed shows `2dc8:310a → 2dc8:301c`. On again: *pad linked*,
   `reconn 1`.
5. Pull the receiver out: *GONE FROM USB*. Plug it into the same port: the panel
   continues; Details shows new `hidrawN`/`eventN` numbers.
6. While doing all that, a game or `evtest` must keep receiving the pad's input
   (WCM never grabs the device).
7. HID, read-only: `./run.sh bc250-wcm hid descriptor 2dc8:310a:2` must decode
   input `0x02` 64 B and output `0x81`, page `0xFF7A`.
   `./run.sh bc250-wcm hid capture 2dc8:310a:2 -t 20 --marks -o ~/rx.jsonl`
   — press buttons, type marks. Note whether *any* report arrives (upstream
   reports the interface stays silent unless asked; "0 reports" is a valid
   result). If access is denied, see [permissions](permissions.md).
8. Report: `--json` output, `~/rx.jsonl`, the event log.

## 10. Exports

`e` and `j` on the dashboard. Open the CSV: rows while PAD2 was off have an
empty `rssi` and state `disconnected` (not 0). The JSON has a summary per pad
and the event list.
