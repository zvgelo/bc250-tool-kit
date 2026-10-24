"""TUI screenshot taken from a pseudo-terminal — a development tool.

Lets the interface layout be inspected without real pads and without resizing
a window by hand. The monitor runs in a pseudo-terminal of the requested size
and, with WCM_DUMP set, writes the screen exactly as curses holds it after
every refresh — that file is what gets printed. (Feeding the raw output to a
terminal emulator such as pyte is not reliable: ncurses uses sequences like
REP that pyte does not implement, and the picture drifts.)

    python3 dev/tuishot.py 160 45                       # picker, demo mode
    python3 dev/tuishot.py 160 45 --keys '1 2 3 4 \\r'   # select four, monitor
    python3 dev/tuishot.py 120 40 --speed 20 --keys ... # fast-forward the demo
    python3 dev/tuishot.py 120 40 --real                # real BlueZ instead of demo

Keys are separated by spaces; \\r is Enter, \\t Tab, \\e Esc, ~N waits N seconds.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import pty
import select
import struct
import sys
import tempfile
import termios
import time

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESCAPES = {"\\r": "\r", "\\t": "\t", "\\e": "\x1b", "\\s": " "}


def main() -> int:
    parser = argparse.ArgumentParser(description="bc250-wcm TUI screenshot")
    parser.add_argument("cols", type=int)
    parser.add_argument("rows", type=int)
    parser.add_argument("--keys", default="", help="space-separated keys sent after start-up")
    parser.add_argument("--warmup", type=float, default=1.5)
    parser.add_argument("--speed", default="1", help="demo timeline speed-up")
    parser.add_argument("--interval", default="0.25")
    parser.add_argument("--real", action="store_true", help="real BlueZ instead of --demo")
    parser.add_argument("--lang", choices=("pl", "en"), default="en")
    parser.add_argument("--extra", default="", help="extra arguments for bc250-wcm")
    args = parser.parse_args()

    dump = os.path.join(tempfile.mkdtemp(prefix="wcm-shot-"), "screen.txt")

    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update(TERM="xterm-256color", LANG="en_US.UTF-8", WCM_DUMP=dump,
                          XDG_STATE_HOME=os.path.dirname(dump),
                          LINES=str(args.rows), COLUMNS=str(args.cols))
        os.chdir(TOOL_DIR)
        argv = [sys.executable, "bin/bc250-wcm", "-i", args.interval, "--lang", args.lang,
                "--no-sudo"]
        if not args.real:
            argv += ["--demo", "--demo-speed", args.speed]
        argv += args.extra.split()
        os.execv(sys.executable, argv)

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", args.rows, args.cols, 0, 0))

    def pump(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if not ready:
                continue
            try:
                data = os.read(fd, 65536)
            except OSError:
                return
            if not data:
                return

    pump(args.warmup)
    for key in args.keys.split():
        if key.startswith("~"):
            pump(float(key[1:]))
            continue
        os.write(fd, ESCAPES.get(key, key).encode())
        pump(0.5)

    try:
        with open(dump, encoding="utf-8") as handle:
            sys.stdout.write(handle.read())
    except OSError:
        sys.stderr.write("no screen dump — did the monitor start?\n")

    os.write(fd, b"q")
    pump(0.5)
    try:
        os.kill(pid, 9)
        os.waitpid(pid, os.WNOHANG)
    except (ProcessLookupError, ChildProcessError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
