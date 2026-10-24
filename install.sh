#!/usr/bin/env bash
# Installs every tool of the kit in one go.
#
#   ./install.sh              user installation of all tools (no root), then asks
#                             whether to add the optional system parts (sudo)
#   ./install.sh --system     the same, and the system parts without asking
#   ./install.sh --user-only  never touch the system, never ask
#
# User installation = each tool's own install.sh: symlinks in ~/.local/bin and
# manual pages in ~/.local/share/man. Nothing is started or enabled — in
# particular bc250-nosuspend blocks nothing until you run `bc250-nosuspend start`.
#
# System parts (one sudo prompt), currently all from bc250-wcm:
#   - the read-only RSSI helper with a sudoers rule for this user, so the monitor
#     no longer asks for a password;
#   - a uaccess udev rule for the 8BitDo receiver's hidraw nodes (skipped when
#     Steam's rule already covers them).
#
# Also repairs what copying the kit without Unix permissions breaks: the
# executable bits, and directories of tools that were renamed since.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tools_dir="${repo}/tools"

# renamed tools: old directory -> new directory
RENAMED=("bc250-btmon:bc250-wcm")

mode="ask"
case "${1:-}" in
    "")            ;;
    --system)      mode="system" ;;
    --user-only)   mode="user" ;;
    -h|--help)     sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)             echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
esac

interactive=0
if [ -t 0 ] && [ -t 1 ]; then
    interactive=1
fi

ask() {
    # ask "question" -> 0 for yes; "no" when not interactive
    local reply
    [ "$interactive" -eq 1 ] || return 1
    read -r -p "$1 [y/N] " reply || return 1
    case "$reply" in y|Y|yes|YES|t|T|tak) return 0 ;; *) return 1 ;; esac
}

section() {
    echo
    echo "── $1 $(printf '─%.0s' $(seq 1 $((60 - ${#1}))))"
}

# --- 1. executable bits -----------------------------------------------------
section "Executable bits"
fixed=0
for file in "${repo}/run.sh" "${repo}/install.sh" "${tools_dir}"/*/install.sh "${tools_dir}"/*/bin/*; do
    [ -f "$file" ] || continue
    if [ ! -x "$file" ]; then
        chmod +x "$file"
        echo "restored: ${file#"${repo}/"}"
        fixed=$((fixed + 1))
    fi
done
[ "$fixed" -eq 0 ] && echo "all in place"

# --- 2. leftovers of renamed tools --------------------------------------------
section "Renamed tools"
leftovers=0
skip=()
for pair in "${RENAMED[@]}"; do
    old="${pair%%:*}"
    new="${pair##*:}"
    [ -d "${tools_dir}/${old}" ] || continue
    [ -d "${tools_dir}/${new}" ] || continue
    leftovers=$((leftovers + 1))
    if git -C "$repo" ls-files --error-unmatch "tools/${old}" >/dev/null 2>&1; then
        # still tracked: this checkout is older than the rename, not a leftover
        echo "tools/${old} is still part of this checkout — update it (git pull) first."
        skip+=("$old")
        continue
    fi
    echo "tools/${old} is a leftover: the tool is now tools/${new}"
    echo "(copying the kit over an old copy does not delete removed files)."
    if [ -d "${repo}/.git" ] && git -C "$repo" status --porcelain -- "tools/${old}" | grep -q .; then
        echo "git sees files there that are not in the repository:"
        git -C "$repo" status --porcelain -- "tools/${old}" | head -5 | sed 's/^/  /'
    fi
    if ask "Remove tools/${old}?"; then
        rm -rf "${tools_dir:?}/${old}"
        echo "removed tools/${old}"
    else
        echo "kept — './run.sh' will keep listing it until it is removed; not installing it"
        skip+=("$old")
    fi
done
[ "$leftovers" -eq 0 ] && echo "none"

# --- 3. every tool, user level ----------------------------------------------------
section "Tools (user level, no root)"
installed=()
failed=()
for dir in "${tools_dir}"/*/; do
    name="$(basename "$dir")"
    script="${dir}install.sh"
    [ -f "$script" ] && [ -f "${dir}bin/${name}" ] || continue
    if [[ " ${skip[*]:-} " == *" ${name} "* ]]; then
        continue
    fi
    echo
    echo "[${name}]"
    # each installer prints the uninstall line; the PATH note comes once, at the end
    if bash "$script" | grep -v -e '^Note: .* is not in \$PATH' -e 'export PATH=' ; then
        installed+=("$name")
    else
        failed+=("$name")
    fi
done

# --- 4. system parts --------------------------------------------------------------
wcm="${tools_dir}/bc250-wcm/install.sh"
if [ -f "$wcm" ] && [ "$mode" != "user" ]; then
    section "System parts (sudo)"
    echo "  - RSSI helper + sudoers rule: bc250-wcm stops asking for a password"
    echo "  - udev uaccess rule for the 8BitDo receiver's hidraw (only if Steam's is missing)"
    if [ "$mode" = "system" ] || ask "Install them now?"; then
        # one prompt for both: the helper asks first and sudo caches the password
        if bash "$wcm" --rssi-helper | sed -n '/^Installing the read-only RSSI helper/,$p' \
                && bash "$wcm" --udev; then
            installed+=("system parts")
        else
            failed+=("system parts")
        fi
    else
        echo "skipped — later: ./install.sh --system"
    fi
fi

# --- summary ------------------------------------------------------------------------
section "Done"
echo "installed: ${installed[*]:-nothing}"
if [ "${#failed[@]}" -gt 0 ]; then
    echo "FAILED:    ${failed[*]}" >&2
fi
case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) ;;
    *) echo
       echo "Note: ~/.local/bin is not in \$PATH — add to ~/.bashrc:"
       echo "  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
echo
echo "Every tool also runs without installing: ./run.sh <tool> or ./run.sh --menu"
[ "${#failed[@]}" -eq 0 ]
