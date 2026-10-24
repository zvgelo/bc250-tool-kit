"""User configuration: an INI file under $XDG_CONFIG_HOME/bc250-wcm/.

INI rather than TOML because configparser exists in every Python 3 the tool
supports. Unknown keys and invalid values are reported, never fatal.
"""

from __future__ import annotations

import configparser
import os
import shutil
from dataclasses import dataclass, field

BACKENDS = ("auto", "helper", "direct", "btmgmt", "none")
SUDO_MODES = ("ask", "never")
COLOR_MODES = ("auto", "256", "8", "none")
GRAPH_MODES = ("auto", "braille", "ascii")


def _xdg(var: str, fallback: str) -> str:
    value = os.environ.get(var)
    if value and os.path.isabs(value):
        return value
    return os.path.join(os.path.expanduser("~"), fallback)


def config_dir() -> str:
    return os.path.join(_xdg("XDG_CONFIG_HOME", ".config"), "bc250-wcm")


def state_dir() -> str:
    return os.path.join(_xdg("XDG_STATE_HOME", ".local/state"), "bc250-wcm")


def data_dir() -> str:
    return os.path.join(_xdg("XDG_DATA_HOME", ".local/share"), "bc250-wcm")


def default_config_path() -> str:
    return os.path.join(config_dir(), "config.ini")


LEGACY_NAME = "bc250-btmon"
MIGRATION_MARK = ".migrated-from-bc250-btmon"


def migrate_legacy() -> list[str]:
    """Copies bc250-btmon's configuration, event log, selection and exports to WCM.

    Copies, never moves: the old files stay where they were, and nothing that
    already exists under the new name is overwritten. Runs once (a marker in
    the new state directory records it). Returns what was done, for the user.
    """
    new_state = state_dir()
    mark = os.path.join(new_state, MIGRATION_MARK)
    if os.path.exists(mark):
        return []
    old_config = os.path.join(_xdg("XDG_CONFIG_HOME", ".config"), LEGACY_NAME)
    old_state = os.path.join(_xdg("XDG_STATE_HOME", ".local/state"), LEGACY_NAME)
    old_data = os.path.join(_xdg("XDG_DATA_HOME", ".local/share"), LEGACY_NAME)
    if not any(os.path.isdir(d) for d in (old_config, old_state, old_data)):
        return []
    done: list[str] = []

    def copy(src: str, dst: str) -> None:
        if not os.path.isfile(src):
            return
        if os.path.exists(dst):
            # keep both: the old history goes next to the new one
            root, ext = os.path.splitext(dst)
            dst = f"{root}-btmon{ext}"
            if os.path.exists(dst):
                return
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        done.append(f"{src} -> {dst}")

    try:
        copy(os.path.join(old_config, "config.ini"), default_config_path())
        copy(os.path.join(old_state, "events.jsonl"), os.path.join(new_state, "events.jsonl"))
        copy(os.path.join(old_state, "events.jsonl.1"), os.path.join(new_state, "events-btmon.jsonl.1"))
        copy(os.path.join(old_state, "selection"), os.path.join(new_state, "selection"))
        old_exports = os.path.join(old_data, "exports")
        if os.path.isdir(old_exports):
            for name in sorted(os.listdir(old_exports)):
                copy(os.path.join(old_exports, name), os.path.join(data_dir(), "exports", name))
        os.makedirs(new_state, exist_ok=True)
        with open(mark, "w", encoding="utf-8") as handle:
            handle.write("\n".join(done) + "\n")
    except OSError as exc:
        done.append(f"migration incomplete: {exc}")
    return done


@dataclass
class Config:
    interval: float = 1.0
    query_timeout: float = 2.5
    history: int = 3600
    backend: str = "auto"
    sudo: str = "ask"
    good: int = -60
    fair: int = -70
    weak: int = -80
    colors: str = "auto"
    graph: str = "auto"
    log_events: bool = True
    event_file: str = field(default_factory=lambda: os.path.join(state_dir(), "events.jsonl"))
    export_dir: str = field(default_factory=lambda: os.path.join(data_dir(), "exports"))
    follow_kernel: bool = True
    remember_selection: bool = True
    selection_file: str = field(default_factory=lambda: os.path.join(state_dir(), "selection"))
    usb: bool = True                # look for USB controllers and 2.4 GHz receivers
    input_activity: bool = True     # count input events from evdev (read-only, never grabbed)
    ascii: bool = False             # --ascii: no Unicode at all, not only the graph

    def rssi_level(self, rssi: int | None) -> str | None:
        """good / fair / weak / poor against the configured thresholds."""
        if rssi is None:
            return None
        if rssi >= self.good:
            return "good"
        if rssi >= self.fair:
            return "fair"
        if rssi >= self.weak:
            return "weak"
        return "poor"


_SCHEMA = {
    ("monitor", "interval"): ("interval", float, (0.2, 60.0)),
    ("monitor", "query_timeout"): ("query_timeout", float, (0.2, 10.0)),
    ("monitor", "history"): ("history", int, (60, 864000)),
    ("rssi", "backend"): ("backend", BACKENDS, None),
    ("rssi", "sudo"): ("sudo", SUDO_MODES, None),
    ("thresholds", "good"): ("good", int, (-127, 20)),
    ("thresholds", "fair"): ("fair", int, (-127, 20)),
    ("thresholds", "weak"): ("weak", int, (-127, 20)),
    ("ui", "colors"): ("colors", COLOR_MODES, None),
    ("ui", "graph"): ("graph", GRAPH_MODES, None),
    ("ui", "remember_selection"): ("remember_selection", bool, None),
    ("log", "events"): ("log_events", bool, None),
    ("log", "event_file"): ("event_file", "path", None),
    ("log", "export_dir"): ("export_dir", "path", None),
    ("kernel", "follow"): ("follow_kernel", bool, None),
    ("usb", "enabled"): ("usb", bool, None),
    ("usb", "input_activity"): ("input_activity", bool, None),
}


def load(path: str | None = None) -> tuple[Config, list[str]]:
    """Reads the file if it exists; returns the config and a list of warnings."""
    cfg = Config()
    warnings: list[str] = []
    path = path or default_config_path()
    if not os.path.exists(path):
        return cfg, warnings
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(path, encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error) as exc:
        return cfg, [f"{path}: {exc}"]

    for section in parser.sections():
        for key, raw in parser.items(section):
            spec = _SCHEMA.get((section, key))
            if spec is None:
                warnings.append(f"{path}: unknown setting [{section}] {key}")
                continue
            attr, kind, bounds = spec
            try:
                value = _convert(raw, kind, bounds)
            except ValueError as exc:
                warnings.append(f"{path}: [{section}] {key} = {raw!r}: {exc}")
                continue
            setattr(cfg, attr, value)

    if not cfg.good > cfg.fair > cfg.weak:
        warnings.append(f"{path}: thresholds must satisfy good > fair > weak — using defaults")
        cfg.good, cfg.fair, cfg.weak = Config.good, Config.fair, Config.weak
    return cfg, warnings


def _convert(raw: str, kind, bounds):
    raw = raw.strip()
    if kind is bool:
        lowered = raw.lower()
        if lowered in ("1", "yes", "true", "on"):
            return True
        if lowered in ("0", "no", "false", "off"):
            return False
        raise ValueError("expected yes/no")
    if kind == "path":
        if not raw:
            raise ValueError("empty path")
        return os.path.expanduser(raw)
    if isinstance(kind, tuple):
        if raw not in kind:
            raise ValueError(f"expected one of {', '.join(kind)}")
        return raw
    value = kind(raw)
    if bounds and not bounds[0] <= value <= bounds[1]:
        raise ValueError(f"outside {bounds[0]}..{bounds[1]}")
    return value


EXAMPLE = """\
# bc250-wcm configuration — copy to ~/.config/bc250-wcm/config.ini
# Every setting is optional; the values below are the defaults.

[monitor]
# seconds between RSSI samples (0.2 .. 60); +/- change it at runtime
interval = 1.0
# how long a single RSSI query may take before it counts as a timeout
query_timeout = 2.5
# samples kept per device for the graph and export (3600 = one hour at 1 s)
history = 3600

[rssi]
# auto | helper | direct | btmgmt | none   (see docs/permissions.md)
backend = auto
# ask: may run `sudo -v` before the interface starts; never: never ask
sudo = ask

[thresholds]
# RSSI colour bands in dBm: >= good is green, >= fair yellow-green,
# >= weak orange, below weak red. They are display bands, not a quality score.
good = -60
fair = -70
weak = -80

[ui]
# auto | 256 | 8 | none
colors = auto
# auto | braille | ascii
graph = auto
# preselect the devices monitored last time
remember_selection = yes

[log]
events = yes
# event_file = ~/.local/state/bc250-wcm/events.jsonl
# export_dir = ~/.local/share/bc250-wcm/exports

[kernel]
# follow Bluetooth messages in the kernel log (journalctl -k)
follow = yes

[usb]
# USB controllers and dedicated 2.4 GHz receivers (sysfs, no privileges)
enabled = yes
# count input events from the controller's evdev node: read-only, never grabbed,
# so games and Steam keep every event. It measures use, not signal strength.
input_activity = yes
"""
