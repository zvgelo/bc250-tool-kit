#!/usr/bin/env bash
# Installs bc250-wcm (Wireless Controller Monitor) for the current user: a
# symlink in ~/.local/bin and the manual pages in ~/.local/share/man. No root
# needed; survives image updates on atomic distributions (Bazzite, Silverblue).
#
#   ./install.sh                      user installation (no root)
#   ./install.sh --rssi-helper        also install the RSSI helper system-wide
#                                     with a sudoers rule for this user (sudo)
#   ./install.sh --remove-rssi-helper remove that helper and rule again (sudo)
#   ./install.sh --udev               hidraw access for `hid capture` (sudo)
#   ./install.sh --remove-udev        remove that rule again (sudo)
#
# Without the helper the monitor asks for the sudo password once per start to
# read Bluetooth link RSSI. See docs/permissions.md for what it may and may not do.
set -euo pipefail

tool_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin_dir="${HOME}/.local/bin"
man_dir="${HOME}/.local/share/man/man1"
man_dir_pl="${HOME}/.local/share/man/pl/man1"

helper_dir="/usr/local/libexec/bc250-wcm"
helper="${helper_dir}/rssi-helper"
sudoers="/etc/sudoers.d/bc250-wcm"
# where bc250-btmon (the tool's former name) installed them
legacy_helper_dir="/usr/local/libexec/bc250-btmon"
legacy_sudoers="/etc/sudoers.d/bc250-btmon"
udev_rule="/etc/udev/rules.d/70-bc250-wcm.rules"

install_user() {
    mkdir -p "$bin_dir" "$man_dir" "$man_dir_pl"

    # copying the repository over a medium without Unix permissions loses the
    # executable bit, and a symlink to a file without it would be useless
    for launcher in bc250-wcm bc250-btmon; do
        if [ ! -x "${tool_dir}/bin/${launcher}" ]; then
            chmod +x "${tool_dir}/bin/${launcher}"
            echo "Restored the executable bit: ${tool_dir}/bin/${launcher}"
        fi
    done

    ln -sf "${tool_dir}/bin/bc250-wcm" "${bin_dir}/bc250-wcm"
    # the former name keeps working; deliberately not `btmon` — BlueZ's HCI tracer
    ln -sf "${tool_dir}/bin/bc250-btmon" "${bin_dir}/bc250-btmon"
    # English by default, Polish under the language path: LANG=pl_PL man bc250-wcm
    ln -sf "${tool_dir}/man/en/bc250-wcm.1" "${man_dir}/bc250-wcm.1"
    ln -sf "${tool_dir}/man/pl/bc250-wcm.1" "${man_dir_pl}/bc250-wcm.1"
    # manual pages under the old name point into a directory that no longer exists
    for old in "${man_dir}/bc250-btmon.1" "${man_dir_pl}/bc250-btmon.1"; do
        if [ -L "$old" ] && [ ! -e "$old" ]; then
            rm -f "$old"
        fi
    done

    echo "bc250-wcm   -> ${bin_dir}/bc250-wcm"
    echo "bc250-btmon -> ${bin_dir}/bc250-btmon  (former name, same program)"
    echo "man (en)    -> ${man_dir}/bc250-wcm.1"
    echo "man (pl)    -> ${man_dir_pl}/bc250-wcm.1"

    if command -v mandb >/dev/null 2>&1; then
        mandb --quiet --user-db 2>/dev/null || true
    fi

    case ":${PATH}:" in
        *":${bin_dir}:"*) ;;
        *) echo
           echo "Note: ${bin_dir} is not in \$PATH — add to ~/.bashrc:"
           echo "  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
    esac

    echo
    echo "Settings, history and exports of bc250-btmon are copied (not moved) on the"
    echo "first start of bc250-wcm."
    echo
    echo "Uninstall:"
    echo "  rm ${bin_dir}/bc250-wcm ${bin_dir}/bc250-btmon ${man_dir}/bc250-wcm.1 ${man_dir_pl}/bc250-wcm.1"
}

install_helper() {
    local user tmp
    user="$(id -un)"
    if [ "$user" = "root" ]; then
        echo "Run this as the user who will use the monitor, not as root." >&2
        exit 1
    fi
    if [ ! -x /usr/bin/python3 ]; then
        echo "/usr/bin/python3 is missing — the helper needs the system Python." >&2
        exit 1
    fi
    echo "Installing the read-only RSSI helper:"
    echo "  ${helper}  (root:root 0755, a copy of bc250wcm/mgmt.py)"
    echo "  ${sudoers}  (${user} may run exactly that file, without arguments)"
    echo

    # a root-owned copy: a rule pointing into the user's home would let anyone
    # with the user's shell edit the file and run arbitrary code as root
    sudo install -d -o root -g root -m 0755 "$helper_dir"
    sudo install -o root -g root -m 0755 "${tool_dir}/bc250wcm/mgmt.py" "$helper"

    tmp="$(mktemp)"
    trap 'rm -f "$tmp"' EXIT
    cat > "$tmp" <<RULE
# bc250-wcm: read the RSSI of live Bluetooth links (mgmt Get Connection
# Information needs CAP_NET_ADMIN). The helper takes no arguments ("" below)
# and accepts only read-only queries on stdin. Remove with:
#   ${tool_dir}/install.sh --remove-rssi-helper
${user} ALL=(root) NOPASSWD: ${helper} ""
RULE
    if ! sudo visudo -cf "$tmp" >/dev/null; then
        echo "visudo rejected the rule — nothing was changed in /etc/sudoers.d." >&2
        exit 1
    fi
    sudo install -o root -g root -m 0440 "$tmp" "$sudoers"
    if [ -e "$legacy_sudoers" ] || [ -d "$legacy_helper_dir" ]; then
        sudo rm -f "$legacy_sudoers" "${legacy_helper_dir}/rssi-helper"
        sudo rmdir "$legacy_helper_dir" 2>/dev/null || true
        echo "Replaced the bc250-btmon helper and its rule."
    fi
    echo "Done. Check: sudo -n ${helper} </dev/null   (prints a 'ready' line and exits)"
}

remove_helper() {
    sudo rm -f "$sudoers" "$helper" "$legacy_sudoers" "${legacy_helper_dir}/rssi-helper"
    sudo rmdir "$helper_dir" "$legacy_helper_dir" 2>/dev/null || true
    echo "Removed ${helper} and ${sudoers} (and any bc250-btmon leftovers)."
}

install_udev() {
    # Steam's 60-steam-input.rules already gives every 2dc8 hidraw node uaccess
    if grep -ls 'idVendor}=="2dc8"' /usr/lib/udev/rules.d/*steam* /etc/udev/rules.d/*steam* \
            2>/dev/null | grep -q .; then
        echo "A Steam udev rule already grants hidraw access for 8BitDo (2dc8) — nothing to do."
        return 0
    fi
    sudo install -o root -g root -m 0644 "${tool_dir}/udev/70-bc250-wcm.rules" "$udev_rule"
    sudo udevadm control --reload
    sudo udevadm trigger --subsystem-match=hidraw --action=change
    echo "Installed ${udev_rule}."
}

remove_udev() {
    sudo rm -f "$udev_rule"
    sudo udevadm control --reload
    echo "Removed ${udev_rule}."
}

case "${1:-}" in
    "")                   install_user ;;
    --rssi-helper)        install_user; echo; install_helper ;;
    --remove-rssi-helper) remove_helper ;;
    --udev)               install_udev ;;
    --remove-udev)        remove_udev ;;
    -h|--help)            sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//' ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
esac
