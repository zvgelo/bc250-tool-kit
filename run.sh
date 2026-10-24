#!/usr/bin/env bash
# Runs a tool from the kit straight out of the repository, without installing.
#
#   ./run.sh                    list the available tools
#   ./run.sh --menu             choose a tool from a numbered menu
#   ./run.sh bc250-mon --demo   run a tool with arguments
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tools_dir="${repo}/tools"

# Tool description: a "<!-- description: ... -->" marker in the README, and when
# absent, the first paragraph line under the title, stripped of markdown.
tool_description() {
    local readme="$1" desc=""
    [ -f "$readme" ] || return 0

    desc="$(sed -n 's/^<!-- *description: *\(.*[^ ]\) *-->$/\1/p' "$readme" | head -1)"
    if [ -z "$desc" ]; then
        desc="$(sed -n '2,10p' "$readme" | grep -m1 -v '^[[:space:]]*$' \
                | sed -e 's/\*\*//g' -e 's/`//g' -e 's/[:.].*$//')"
    fi
    printf '%s' "${desc:0:72}"
}

list_tools() {
    echo "Tools in the bc250-tool-kit:"
    echo
    local found=0
    for dir in "${tools_dir}"/*/; do
        local name entry desc
        name="$(basename "$dir")"
        entry="${dir}bin/${name}"
        [ -f "$entry" ] || continue
        found=1
        desc="$(tool_description "${dir}README.md")"
        printf '  %-17s %s\n' "$name" "$desc"
    done
    [ "$found" -eq 1 ] || echo "  (no tools found under tools/*/bin/)"
    echo
    echo "Usage: ./run.sh <tool> [arguments]"
    echo "A tool's help: ./run.sh <tool> --help"
    echo "Menu: ./run.sh --menu"
}

# Numbered menu: prints the tools, reads a number, sets $tool. Returns 1 on quit.
menu() {
    local names=() dir name entry i choice
    for dir in "${tools_dir}"/*/; do
        name="$(basename "$dir")"
        entry="${dir}bin/${name}"
        [ -f "$entry" ] && names+=("$name")
    done
    if [ "${#names[@]}" -eq 0 ]; then
        echo "No tools found under tools/*/bin/." >&2
        return 1
    fi
    echo "bc250-tool-kit"
    echo
    for i in "${!names[@]}"; do
        printf '  %2d) %-17s %s\n' "$((i + 1))" "${names[$i]}" \
            "$(tool_description "${tools_dir}/${names[$i]}/README.md")"
    done
    echo
    while true; do
        read -r -p "Tool number (q to quit): " choice || return 1
        case "$choice" in
            q|Q|"") return 1 ;;
            *[!0-9]*) ;;
            *) if [ "$choice" -ge 1 ] && [ "$choice" -le "${#names[@]}" ]; then
                   tool="${names[$((choice - 1))]}"
                   return 0
               fi ;;
        esac
        echo "Choose 1-${#names[@]}."
    done
}

if [ $# -eq 0 ] || [ "${1}" = "-h" ] || [ "${1}" = "--help" ]; then
    list_tools
    exit 0
fi

if [ "${1}" = "--menu" ] || [ "${1}" = "-m" ]; then
    shift
    menu || exit 0
else
    tool="$1"
    shift
fi

# tools that were renamed keep their old name working
case "$tool" in
    bc250-btmon) echo "Note: bc250-btmon is now bc250-wcm." >&2; tool="bc250-wcm" ;;
esac

entry="${tools_dir}/${tool}/bin/${tool}"
if [ ! -f "$entry" ]; then
    echo "Unknown tool '${tool}'." >&2
    echo >&2
    list_tools >&2
    exit 1
fi

if [ -x "$entry" ]; then
    exec "$entry" "$@"
fi

# No executable bit — typical after copying the repository over a medium or a
# network share without Unix permissions. Run it through the shebang interpreter.
echo "Note: ${entry} has no executable bit — running it through its interpreter." >&2
echo "      Permanent fix: chmod +x run.sh tools/*/bin/* tools/*/install.sh" >&2

shebang="$(sed -n '1s/^#![[:space:]]*//p' "$entry")"
case "$shebang" in
    *python*) exec python3 "$entry" "$@" ;;
    *bash*)   exec bash "$entry" "$@" ;;
    */sh*)    exec sh "$entry" "$@" ;;
    *)
        echo "Do not know how to run ${entry} (shebang: '${shebang:-none}')." >&2
        exit 1
        ;;
esac
