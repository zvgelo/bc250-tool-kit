# How the suspend block works

**English** · [Polski](how-it-works.pl.md)

## Layers of suspend in Linux

Suspend can be initiated by several independent layers, and disabling only one of
them is usually not enough:

| Layer | Example | Covered by `bc250-nosuspend` |
|---|---|---|
| systemd targets | `systemctl suspend`, the power button | **yes** — target masking |
| logind idling | `IdleAction` after `IdleActionSec` | **yes** — an `IdleAction=ignore` drop-in |
| Desktop settings | KDE Powerdevil, Gaming Mode | not needed — the request hits a masked target anyway |
| Screen blanking | DPMS, screensaver | **no** — a separate mechanism, see below |
| Temporary block | `systemd-inhibit` | no — that is the system tool for one-off jobs |

Masking systemd targets works precisely because it sits **below** the desktop:
KDE and Gaming Mode do not suspend the system themselves, they ask logind to, and
logind starts `suspend.target`. A masked target is a symlink to `/dev/null`, so
the request fails no matter who made it.

## Files that get created

| Path | Role |
|---|---|
| `/etc/systemd/system/sleep.target` → `/dev/null` | target mask (likewise for the other four) |
| `/etc/systemd/logind.conf.d/10-bc250-nosuspend.conf` | `IdleAction=ignore` |
| `/var/lib/bc250-nosuspend/previous-mask-state` | which targets were masked **before** the block |

The state file is why `stop` is safe: without it, removing the block would also
unmask targets that somebody else had masked earlier. It is written only on the
first `start`; running `start` again does not overwrite the original state.

## The masked targets

```
sleep.target                    the umbrella target the others pull in
suspend.target                  suspend to RAM (S3)
hibernate.target                hibernate to disk (S4)
hybrid-sleep.target             suspend with a simultaneous disk image
suspend-then-hibernate.target   suspend, then hibernate after a delay
```

## Reversing the change by hand

Should the tool be unavailable, two commands undo everything:

```bash
sudo systemctl unmask sleep.target suspend.target hibernate.target \
                      hybrid-sleep.target suspend-then-hibernate.target
sudo rm -f /etc/systemd/logind.conf.d/10-bc250-nosuspend.conf
sudo systemctl reload systemd-logind
```

## Atomic distributions (Bazzite, Silverblue)

The block works normally there and **does not require package layering**: both
`/etc` and `/var` are writable on an ostree image, and the masks are ordinary
symlinks in `/etc/systemd/system`. An image update does not remove them, because
`rpm-ostree` merges changes in `/etc`.

One side effect: since this is a change under `/etc`, it will show up in
`rpm-ostree status` as a local configuration modification. That is normal.

## Verifying that the block works

```bash
bc250-nosuspend status          # exit code 0 = blocked
systemctl suspend               # should refuse: Unit suspend.target is masked
```

The second command is safe while the block is in place — failing is exactly the
point.

## Why `IdleAction=ignore` alone is not enough

`IdleAction` controls only the reaction to **idling**. It will not stop a suspend
triggered by hand from a menu, by the power button, or by closing a lid. That is
why the tool sets both mechanisms at once: the masks block requests, and
`IdleAction` disables the automatic trigger.

## What about screen blanking?

That is an entirely different mechanism — it does not suspend the system, it only
blanks the display. The desktop environment controls it:

```bash
# KDE Plasma (user session)
kwriteconfig6 --file powermanagementprofilesrc --group AC --group DPMSControl --key idleTime 0

# Gaming Mode: Settings → Power
```

We deliberately leave it alone: desktop settings live in the user profile, change
between Plasma releases, and would require guessing which session is active. The
system-level block is unambiguous and reversible with a single command.
