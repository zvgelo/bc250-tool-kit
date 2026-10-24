"""`bc250-wcm hid …` — passive research of HID interfaces, vendor-defined ones above all.

    hid list                     every hidraw node: selector, name, reports, access
    hid descriptor SEL           the report descriptor, raw and decoded
    hid capture SEL [-t S] [-o FILE] [--marks]
                                 read input reports passively, print them as hex
    hid analyze FILE [FILE2]     which bytes change, how often, and with which marks

SEL is hidraw3, /dev/hidraw3, or — stable across re-plugging — vid:pid:interface
such as 2dc8:310a:2.

Nothing here writes to a device: nodes are opened O_RDONLY and no output or
feature report exists in this code. On the 8BitDo 2.4 GHz receiver the vendor
interface is also the firmware-update channel; sending it undocumented reports
can wipe its pairing or worse, which is exactly why this tool cannot.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import hid
from .i18n import t


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bc250-wcm hid", description=t("hid.description"))
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list", help=t("hid.list"))
    p_list.add_argument("--json", action="store_true")
    p_desc = sub.add_parser("descriptor", help=t("hid.descriptor"))
    p_desc.add_argument("selector")
    p_cap = sub.add_parser("capture", help=t("hid.capture"))
    p_cap.add_argument("selector")
    p_cap.add_argument("-t", "--seconds", type=float, default=0.0, help=t("hid.capture.seconds"))
    p_cap.add_argument("-o", "--output", help=t("hid.capture.output"))
    p_cap.add_argument("--marks", action="store_true", help=t("hid.capture.marks"))
    p_cap.add_argument("--quiet", action="store_true", help=t("hid.capture.quiet"))
    p_ana = sub.add_parser("analyze", help=t("hid.analyze"))
    p_ana.add_argument("file")
    p_ana.add_argument("other", nargs="?")
    p_ana.add_argument("--window", type=float, default=1.0, help=t("hid.analyze.window"))
    return parser


def main(argv: list[str], sysfs: str = hid.SYSFS, dev_root: str = "/dev") -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "list":
        return cmd_list(hid.list_hidraw(sysfs, dev_root), args.json)
    if args.cmd == "analyze":
        return cmd_analyze(args.file, args.other, args.window)
    nodes = hid.list_hidraw(sysfs, dev_root)
    node = hid.select_hidraw(args.selector, nodes)
    if node is None:
        sys.stderr.write(t("hid.notfound", selector=args.selector) + "\n")
        return 1
    if args.cmd == "descriptor":
        return cmd_descriptor(node)
    return cmd_capture(node, args, dev_root)


def _reports_text(desc: hid.Descriptor | None) -> str:
    if desc is None:
        return "?"
    parts = []
    for rep in desc.reports:
        rid = f"0x{rep.report_id:02X}" if rep.report_id else "-"
        parts.append(f"{rep.kind[:3]} {rid} {rep.total_bytes}B")
    return ", ".join(parts) or "—"


def cmd_list(nodes: list[hid.HidrawInfo], as_json: bool) -> int:
    if as_json:
        json.dump([{
            "node": n.name, "selector": n.selector, "hid_id": n.hid_id, "name": n.hid_name,
            "phys": n.hid_phys, "usb_port": n.usb_port, "interface": n.interface, "access": n.access,
            "vendor_defined": bool(n.descriptor and n.descriptor.vendor_defined),
            "usage_pages": [hid.usage_page_name(p) for p in (n.descriptor.top_pages if n.descriptor else [])],
            "reports": [{"kind": r.kind, "id": r.report_id, "bytes": r.total_bytes}
                        for r in (n.descriptor.reports if n.descriptor else [])],
        } for n in nodes], sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if not nodes:
        print(t("hid.none"))
        return 0
    for n in nodes:
        vendor = n.descriptor is not None and n.descriptor.vendor_defined
        pages = ", ".join(hid.usage_page_name(p) for p in (n.descriptor.top_pages if n.descriptor else []))
        print(f"{n.name:<9} {n.selector:<16} {n.hid_name or '?'}")
        print(f"          {t('hid.pages')}: {pages or '?'}{'   ← ' + t('hid.vendor') if vendor else ''}")
        print(f"          {t('hid.reports')}: {_reports_text(n.descriptor)}")
        print(f"          {n.hid_phys or ''}   {t('hid.access')}: {t('hid.access.' + n.access)}")
    return 0


def cmd_descriptor(node: hid.HidrawInfo) -> int:
    print(f"{node.name}  {node.selector}  {node.hid_name or '?'}")
    print(f"HID_ID={node.hid_id}  HID_PHYS={node.hid_phys}")
    print()
    raw = node.descriptor_raw
    for i in range(0, len(raw), 16):
        print("  " + hid.descriptor_hex(raw[i:i + 16]))
    desc = node.descriptor
    if desc is None:
        print(t("hid.nodescriptor"))
        return 1
    print()
    for depth, ctype, page, usage in desc.collections:
        print(f"  {'  ' * depth}{t('hid.collection')} {hid.COLLECTIONS.get(ctype, ctype)}: "
              f"{hid.usage_page_name(page)}, usage 0x{usage:02X}")
    print()
    for rep in desc.reports:
        rid = f"0x{rep.report_id:02X}" if rep.report_id else t("hid.noid")
        pages = ", ".join(hid.usage_page_name(p) for p in sorted(rep.usage_pages))
        print(f"  {rep.kind:<8} {rid:<6} {t('hid.size', payload=rep.payload_bytes, total=rep.total_bytes)}"
              f"  {pages}")
    for error in desc.errors:
        print("  ! " + error)
    if desc.vendor_defined:
        print()
        print(t("hid.vendor.note"))
    return 0


def cmd_capture(node: hid.HidrawInfo, args, dev_root: str) -> int:
    path = os.path.join(dev_root, node.name)
    try:
        fd = hid.open_readonly(path)
    except PermissionError:
        sys.stderr.write(t("hid.denied", path=path) + "\n")
        return 3
    except OSError as exc:
        sys.stderr.write(f"{path}: {exc}\n")
        return 1
    expected = {r.report_id: r.total_bytes for r in (node.descriptor.reports if node.descriptor else [])
                if r.kind == "input"}
    sys.stderr.write(t("hid.capturing", path=path, name=node.hid_name or "?") + "\n")
    if args.marks:
        sys.stderr.write(t("hid.marks.howto") + "\n")
    previous: dict[int, bytes] = {}
    start = time.time()

    def show(stamp: float, data: bytes) -> None:
        if args.quiet:
            return
        rid = data[0] if expected and 0 not in expected else 0
        line = hid.hex_line(data, previous.get(rid))
        previous[rid] = data
        size_note = ""
        if expected and expected.get(rid) not in (None, len(data)):
            size_note = "  " + t("hid.badsize", got=len(data), want=expected.get(rid))
        print(f"{stamp - start:9.3f}  {line}{size_note}", flush=True)

    try:
        cap = hid.capture(fd, args.seconds, on_report=show,
                          marks_fd=sys.stdin.fileno() if args.marks else None)
    except KeyboardInterrupt:
        cap = None
    finally:
        os.close(fd)
    if cap is None:
        # Ctrl-C: what was shown is all there is; capture() keeps no partial state
        return 0
    cap.meta = {"node": node.name, "selector": node.selector, "hid_id": node.hid_id,
                "name": node.hid_name, "descriptor": node.descriptor_raw.hex(),
                "expected_input_sizes": {str(k): v for k, v in expected.items()}, **cap.meta}
    sys.stderr.write(t("hid.captured", reports=len(cap.reports), marks=len(cap.marks)) + "\n")
    if not cap.reports:
        sys.stderr.write(t("hid.nothing") + "\n")
    if args.output:
        hid.save_capture(cap, args.output)
        sys.stderr.write(t("hid.saved", path=args.output) + "\n")
    return 0


def cmd_analyze(path: str, other: str | None, window: float) -> int:
    try:
        cap = hid.load_capture(path)
        cap2 = hid.load_capture(other) if other else None
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    expected = {int(k): v for k, v in (cap.meta.get("expected_input_sizes") or {}).items()}
    numbered = bool(expected) and 0 not in expected
    print(t("hid.analysis", path=path, reports=len(cap.reports), marks=len(cap.marks)))
    groups = hid.split_by_id(cap.reports, numbered)
    for rid, reports in sorted(groups.items()):
        stats, malformed = hid.byte_stats(reports, expected.get(rid))
        rate = hid.rate(reports)
        print()
        print(t("hid.report.header", rid=f"0x{rid:02X}" if numbered else "-", count=len(reports),
                rate="—" if rate is None else f"{rate:.1f}"))
        if malformed:
            print("  " + t("hid.malformed", count=malformed, want=expected.get(rid)))
        changing = [s for s in stats if not s.constant]
        constant = [s for s in stats if s.constant]
        print("  " + t("hid.constant", count=len(constant)))
        for s in changing:
            top = sorted(s.values.items(), key=lambda kv: -kv[1])[:6]
            print(f"  [{s.offset:2d}] {len(s.values):3d} {t('hid.values')}  "
                  f"{s.lo:02x}..{s.hi:02x}  " + " ".join(f"{v:02x}×{c}" for v, c in top))
        if cap2 is not None:
            other_stats, _ = hid.byte_stats(hid.split_by_id(cap2.reports, numbered).get(rid, []),
                                            expected.get(rid))
            diffs = [i for i in range(min(len(stats), len(other_stats)))
                     if set(stats[i].values) != set(other_stats[i].values)]
            print("  " + t("hid.compare", path=other, offsets=", ".join(map(str, diffs)) or "—"))
    for label, stamp, offsets in hid.correlate_marks(cap, window):
        print(t("hid.mark", label=label, offsets=", ".join(map(str, offsets)) or t("hid.mark.none")))
    return 0
