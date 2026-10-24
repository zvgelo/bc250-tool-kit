#!/usr/bin/env bash
# Installs bc250-nosuspend for the current user: a symlink in ~/.local/bin and
# the manual pages in ~/.local/share/man. No root needed; survives image updates
# on atomic distributions (Bazzite, Silverblue).
set -euo pipefail

tool_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin_dir="${HOME}/.local/bin"
man_dir="${HOME}/.local/share/man/man1"
man_dir_pl="${HOME}/.local/share/man/pl/man1"

mkdir -p "$bin_dir" "$man_dir" "$man_dir_pl"

# copying the repository over a medium without Unix permissions loses the
# executable bit, and a symlink to a file without it would be useless
if [ ! -x "${tool_dir}/bin/bc250-nosuspend" ]; then
    chmod +x "${tool_dir}/bin/bc250-nosuspend"
    echo "Restored the executable bit: ${tool_dir}/bin/bc250-nosuspend"
fi

ln -sf "${tool_dir}/bin/bc250-nosuspend" "${bin_dir}/bc250-nosuspend"
# English by default, Polish under the language path: LANG=pl_PL man bc250-nosuspend
ln -sf "${tool_dir}/man/en/bc250-nosuspend.1" "${man_dir}/bc250-nosuspend.1"
ln -sf "${tool_dir}/man/pl/bc250-nosuspend.1" "${man_dir_pl}/bc250-nosuspend.1"

echo "bc250-nosuspend -> ${bin_dir}/bc250-nosuspend"
echo "man (en)        -> ${man_dir}/bc250-nosuspend.1"
echo "man (pl)        -> ${man_dir_pl}/bc250-nosuspend.1"

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
echo "Uninstall:"
echo "  rm ${bin_dir}/bc250-nosuspend ${man_dir}/bc250-nosuspend.1 ${man_dir_pl}/bc250-nosuspend.1"
