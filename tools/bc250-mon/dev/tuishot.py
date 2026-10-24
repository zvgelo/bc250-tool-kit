"""TUI screenshot taken from a pseudo-terminal — a development tool.

Lets the interface layout be inspected without a physical BC-250 and without
resizing a window by hand. Requires the `pyte` package (see requirements-dev.txt).

    python3 dev/tuishot.py 200 50              # dashboard in demo mode
    python3 dev/tuishot.py 120 40 --keys $'\\t'  # sensors view
    python3 dev/tuishot.py 120 40 --real       # real hardware instead of demo
"""

from __future__ import annotations

import argparse
import fcntl
import os
import pty
import select
import struct
import sys
import termios
import time

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    parser = argparse.ArgumentParser(description="bc250-mon TUI screenshot")
    parser.add_argument("cols", type=int)
    parser.add_argument("rows", type=int)
    parser.add_argument("--keys", default="", help="keys sent after start-up")
    parser.add_argument("--warmup", type=float, default=2.5, help="seconds spent collecting samples")
    parser.add_argument("--interval", default="0.15", help="the monitor's refresh interval")
    parser.add_argument("--real", action="store_true", help="real hardware instead of --demo")
    parser.add_argument("--lang", choices=("pl", "en"), help="interface language")
    args = parser.parse_args()

    try:
        import pyte
    except ImportError:
        sys.stderr.write("pyte is missing — install it: pip install -r dev/requirements-dev.txt\n")
        return 2

    screen = pyte.Screen(args.cols, args.rows)
    stream = pyte.Stream(screen)

    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update(TERM="xterm-256color", LANG="pl_PL.UTF-8",
                          LINES=str(args.rows), COLUMNS=str(args.cols))
        os.chdir(TOOL_DIR)
        argv = [sys.executable, "bin/bc250-mon", "-i", args.interval]
        if not args.real:
            argv.append("--demo")
        if args.lang:
            argv += ["--lang", args.lang]
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
            stream.feed(data.decode("utf-8", "replace"))

    pump(args.warmup)
    for key in args.keys:
        os.write(fd, key.encode())
        pump(0.8)

    for line in screen.display:
        print(line.rstrip())

    os.write(fd, b"q")
    time.sleep(0.2)
    try:
        os.kill(pid, 9)
        os.waitpid(pid, os.WNOHANG)
    except (ProcessLookupError, ChildProcessError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
