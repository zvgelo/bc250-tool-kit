#!/usr/bin/env bash
# bc250-paddles tests in a sandboxed HOME with stubbed flatpak and systemctl —
# they never touch the real system or the real Steam.
set -uo pipefail

TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL="${TOOL_DIR}/bin/bc250-paddles"

passed=0
failed=0

check() {
    local label="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        printf '  ok    %s\n' "$label"
        passed=$((passed + 1))
    else
        printf '  FAIL  %s\n        expected: %s\n        actual:   %s\n' \
               "$label" "$expected" "$actual"
        failed=$((failed + 1))
    fi
}

check_contains() {
    local label="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) printf '  ok    %s\n' "$label"; passed=$((passed + 1)) ;;
        *) printf '  FAIL  %s\n        missing fragment: %s\n' "$label" "$needle"
           failed=$((failed + 1)) ;;
    esac
}

check_absent() {
    local label="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) printf '  FAIL  %s\n        unexpected fragment: %s\n' "$label" "$needle"
                     failed=$((failed + 1)) ;;
        *) printf '  ok    %s\n' "$label"; passed=$((passed + 1)) ;;
    esac
}

GUID="03000000c82d00001c30000011010000"

# --- environment stub ------------------------------------------------------

setup() {
    SANDBOX="$(mktemp -d)"
    export HOME="${SANDBOX}/home"
    export XDG_CONFIG_HOME="${HOME}/.config"
    export FLATPAK_USER_DIR="${HOME}/.local/share/flatpak"
    NATIVE="${HOME}/.local/share/Steam"
    FLATPAK_STEAM="${HOME}/.var/app/com.valvesoftware.Steam/.local/share/Steam"
    OVERRIDE="${FLATPAK_USER_DIR}/overrides/com.valvesoftware.Steam"
    ENVD="${XDG_CONFIG_HOME}/environment.d/90-bc250-paddles.conf"
    mkdir -p "$HOME" "${SANDBOX}/stub" "${SANDBOX}/proc"
    : > "${SANDBOX}/input-devices"
    : > "${SANDBOX}/systemctl.log"

    # flatpak stub: "override --user --env=K=V APP" written like flatpak does
    cat > "${SANDBOX}/stub/flatpak" <<'STUB'
#!/usr/bin/env bash
[ "$1" = "override" ] || exit 0
env_arg="${3#--env=}"
key="${env_arg%%=*}"
file="${FLATPAK_USER_DIR}/overrides/$4"
mkdir -p "$(dirname "$file")"
touch "$file"
if ! grep -qx '\[Environment\]' "$file"; then
    printf '[Environment]\n' >> "$file"
fi
grep -v "^${key}=" "$file" > "${file}.tmp" || true
awk -v line="$env_arg" '{ print } $0 == "[Environment]" { print line }' "${file}.tmp" > "$file"
rm -f "${file}.tmp"
STUB
    cat > "${SANDBOX}/stub/systemctl" <<'STUB'
#!/usr/bin/env bash
echo "$*" | cut -c1-60 >> "$SYSTEMCTL_LOG"
STUB
    chmod +x "${SANDBOX}/stub/flatpak" "${SANDBOX}/stub/systemctl"

    export SYSTEMCTL_LOG="${SANDBOX}/systemctl.log"
    export BC250_PADDLES_FLATPAK="${SANDBOX}/stub/flatpak"
    export BC250_PADDLES_SYSTEMCTL="${SANDBOX}/stub/systemctl"
    export BC250_PADDLES_PROC="${SANDBOX}/proc"
    export BC250_PADDLES_INPUT_DEVICES="${SANDBOX}/input-devices"
    export BC250_PADDLES_SYS="${SANDBOX}/sys"
    export NO_COLOR=1
    # the tests check message content, so the language must be explicit
    export BC250_LANG=en
}

teardown() {
    rm -rf "$SANDBOX"
}

install_native() { mkdir -p "${NATIVE}/ubuntu12_32" "${NATIVE}/logs"; }
install_flatpak() { mkdir -p "${FLATPAK_STEAM}/ubuntu12_32" "${FLATPAK_STEAM}/logs"; }

fake_steam_process() {   # pid kind with-mapping(yes/no)
    local dir="${SANDBOX}/proc/$1"
    mkdir -p "$dir"
    printf 'steam\n' > "${dir}/comm"
    {
        printf 'HOME=/home/x\0'
        [ "$2" = "flatpak" ] && printf 'FLATPAK_ID=com.valvesoftware.Steam\0'
        [ "$3" = "yes" ] && printf 'SDL_GAMECONTROLLERCONFIG=%s,8BitDo\0' "$GUID"
    } > "${dir}/environ"
}

fake_pad() {   # bus product handler [uniq]
    cat >> "${SANDBOX}/input-devices" <<EOF
I: Bus=$1 Vendor=2dc8 Product=$2 Version=0111
N: Name="8BitDo Ultimate 2C Wireless Controller"
U: Uniq=${4:-}
H: Handlers=$3 js0

EOF
}

# --- tests ------------------------------------------------------------------

echo "no Steam installed"
setup
out="$("$TOOL" status)"; code=$?
check "status exits with code 1" "1" "$code"
check_contains "says Steam was not found" "Steam not found" "$out"
"$TOOL" on > /dev/null 2>&1
check "on without Steam fails" "1" "$?"
check "and creates nothing" "1" "$([ -e "$ENVD" ] || [ -e "$OVERRIDE" ]; echo $?)"
teardown

echo "native Steam: on / status / off"
setup
install_native
out="$("$TOOL" on --dry-run)"
check "dry-run writes no file" "1" "$([ -f "$ENVD" ]; echo $?)"
check_contains "dry-run prints the file" "90-bc250-paddles.conf" "$out"
check "dry-run calls no systemctl" "0" "$(grep -c . "$SYSTEMCTL_LOG")"
"$TOOL" on > /dev/null
check "environment.d file written" "0" "$([ -f "$ENVD" ]; echo $?)"
check_contains "file holds the quoted mapping" "SDL_GAMECONTROLLERCONFIG=\"${GUID}," "$(cat "$ENVD")"
check_contains "mapping carries crc and paddles" "crc:6fed" "$(cat "$ENVD")"
check_contains "mapping carries paddle1:b5,paddle2:b2" "paddle1:b5,paddle2:b2" "$(cat "$ENVD")"
check_contains "user manager env set" "--user set-environment SDL_GAMECONTROLLERCONFIG=" "$(cat "$SYSTEMCTL_LOG")"
# the file must be readable by the shell too: gamescope sessions source it
check_contains "the file sources as shell" "$GUID" "$(bash -c ". '$ENVD'; printf %s \"\$SDL_GAMECONTROLLERCONFIG\"")"
out="$("$TOOL" status)"; code=$?
check "status exits with code 0" "0" "$code"
check_contains "status reports the mapping on" "MAPPING ON" "$out"
out="$("$TOOL" on)"
check_contains "a repeated on is safe" "already set" "$out"
"$TOOL" off > /dev/null
check "file removed" "1" "$([ -f "$ENVD" ]; echo $?)"
check_contains "user manager env unset" "--user unset-environment SDL_GAMECONTROLLERCONFIG" "$(cat "$SYSTEMCTL_LOG")"
out="$("$TOOL" status)"; code=$?
check "status back to code 1" "1" "$code"
out="$("$TOOL" off)"
check_contains "a repeated off is safe" "was not set" "$out"
teardown

echo "native: a foreign value is left alone"
setup
install_native
mkdir -p "$(dirname "$ENVD")"
printf 'SDL_GAMECONTROLLERCONFIG="somebody,else"\n' > "$ENVD"
"$TOOL" on > /dev/null 2>&1
check "on refuses" "1" "$?"
check_contains "the foreign value stays" "somebody,else" "$(cat "$ENVD")"
out="$("$TOOL" status)"
check_contains "status names it" "set by somebody else" "$out"
teardown

echo "native: a later environment.d file is reported"
setup
install_native
"$TOOL" on > /dev/null
printf 'SDL_GAMECONTROLLERCONFIG="other"\n' > "${XDG_CONFIG_HOME}/environment.d/95-other.conf"
out="$("$TOOL" status)"
check_contains "shadowing file named" "95-other.conf" "$out"
teardown

echo "Flatpak Steam: on / status / off"
setup
install_flatpak
"$TOOL" on > /dev/null
check "override file created" "0" "$([ -f "$OVERRIDE" ]; echo $?)"
check_contains "override holds the mapping" "SDL_GAMECONTROLLERCONFIG=${GUID}," "$(cat "$OVERRIDE")"
check "no systemctl for Flatpak" "0" "$(grep -c . "$SYSTEMCTL_LOG")"
out="$("$TOOL" status)"; code=$?
check "status exits with code 0" "0" "$code"
check_contains "status says override" "set (Flatpak override)" "$out"
"$TOOL" off > /dev/null
check "override file removed when it held only our key" "1" "$([ -f "$OVERRIDE" ]; echo $?)"
teardown

echo "Flatpak: other overrides survive off"
setup
install_flatpak
mkdir -p "$(dirname "$OVERRIDE")"
printf '[Context]\nfilesystems=/mnt/games;\n\n[Environment]\nMANGOHUD=1\n' > "$OVERRIDE"
"$TOOL" on > /dev/null
"$TOOL" off > /dev/null
check_contains "filesystems kept" "filesystems=/mnt/games;" "$(cat "$OVERRIDE")"
check_contains "MANGOHUD kept" "MANGOHUD=1" "$(cat "$OVERRIDE")"
check_absent "our variable gone" "SDL_GAMECONTROLLERCONFIG" "$(cat "$OVERRIDE")"
teardown

echo "Flatpak: a leftover unset-environment entry is cleared"
setup
install_flatpak
mkdir -p "$(dirname "$OVERRIDE")"
printf '[Context]\nunset-environment=SDL_GAMECONTROLLERCONFIG;FOO;\n' > "$OVERRIDE"
out="$("$TOOL" status)"
check_contains "status warns about it" "unset-environment list" "$out"
"$TOOL" on > /dev/null
check_contains "FOO stays on the list" "unset-environment=FOO;" "$(cat "$OVERRIDE")"
check_absent "our variable left the list" "unset-environment=SDL" "$(cat "$OVERRIDE")"
teardown

echo "Flatpak: a foreign value is left alone"
setup
install_flatpak
mkdir -p "$(dirname "$OVERRIDE")"
printf '[Environment]\nSDL_GAMECONTROLLERCONFIG=other\n' > "$OVERRIDE"
"$TOOL" on > /dev/null 2>&1
check "on refuses" "1" "$?"
out="$("$TOOL" off)"
check_contains "off does not touch it" "was not set" "$out"
check_contains "the foreign value stays" "SDL_GAMECONTROLLERCONFIG=other" "$(cat "$OVERRIDE")"
teardown

echo "both installations and --target"
setup
install_native
install_flatpak
"$TOOL" on --target native > /dev/null
out="$("$TOOL" status)"; code=$?
check "only one of two set: code 2" "2" "$code"
check_contains "partial mapping reported" "partial mapping (1/2)" "$out"
"$TOOL" on > /dev/null
"$TOOL" status > /dev/null; code=$?
check "both set: code 0" "0" "$code"
"$TOOL" status --target flatpak > /dev/null; code=$?
check "status --target flatpak alone: code 0" "0" "$code"
"$TOOL" on --target nonsens > /dev/null 2>&1
check "an unknown target fails" "1" "$?"
teardown

echo "running Steam and its log"
setup
install_native
install_flatpak
fake_steam_process 4242 native yes
fake_steam_process 5151 flatpak no
printf '[2026-10-24 21:40:41] SDL Mapping for 2dc8/301c: 0300ed6f%s,8BitDo,a:b0,platform:Linux,\n' "${GUID:8}" \
    > "${NATIVE}/logs/controller.txt"
printf '[2026-10-24 21:34:02] SDL Mapping for 2dc8/301c: 0300ed6f%s,8BitDo,crc:6fed,paddle1:b5,paddle2:b2,\n' "${GUID:8}" \
    > "${FLATPAK_STEAM}/logs/controller.txt"
out="$("$TOOL" status)"
check_contains "native process has the mapping" "pid 4242 (native): mapping in the process environment" "$out"
check_contains "flatpak process needs a restart" "pid 5151 (flatpak): no mapping" "$out"
check_contains "native log without paddles" "native: 2026-10-24 21:40:41 — WITHOUT paddles" "$out"
check_contains "flatpak log with paddles" "flatpak: 2026-10-24 21:34:02 — with paddles" "$out"
teardown

echo "pads"
setup
install_native
out="$("$TOOL" status)"
check_contains "no pad" "no pad connected" "$out"
fake_pad 0003 301c event18
fake_pad 0003 310a event20
fake_pad 0005 301b event21
out="$("$TOOL" status)"
check_contains "DInput pad recognised" "event18  2dc8:301c DInput" "$out"
check_contains "XInput pad warned about" "event20  2dc8:310a XInput — L4/R4 are NOT sent" "$out"
check_contains "Bluetooth pad recognised" "event21  2dc8:301b Bluetooth" "$out"
check_contains "three pads counted" "3 pads — one mapping covers them all" "$out"
teardown

echo "four pads on four receivers"
setup
install_native
for n in 1 2 3 4; do
    fake_pad 0003 301c "event1${n}" "SERIAL000${n}"
done
fake_pad 0003 301c event19      # the same model with no serial reported
out="$("$TOOL" status)"
check "every DInput pad listed" "5" "$(printf '%s\n' "$out" | grep -c '2dc8:301c DInput')"
check_contains "receiver serials shown" "serial: SERIAL0004" "$out"
check_contains "a missing serial shown as a dash" "serial: -" "$out"
check_contains "all counted" "5 pads" "$out"
# an unrelated device after the pads must not leak its handler
printf 'I: Bus=0003 Vendor=046d Product=c52b Version=0111\nN: Name="Logitech"\nH: Handlers=event30 kbd\n\n' \
    >> "${SANDBOX}/input-devices"
out="$("$TOOL" status)"
check_absent "other vendors ignored" "event30" "$out"
teardown

echo "XInput receiver: keyboard and mouse are not pads"
setup
install_native
# as on a BC-250: xpad pad without uniq, plus the receiver's keyboard and mouse
fake_pad 0003 310a event2
printf 'I: Bus=0003 Vendor=2dc8 Product=310a Version=0110\nU: Uniq=A1B2C3D4E5\nH: Handlers=sysrq kbd leds event3\n\n' \
    >> "${SANDBOX}/input-devices"
printf 'I: Bus=0003 Vendor=2dc8 Product=310a Version=0110\nU: Uniq=A1B2C3D4E5\nH: Handlers=mouse0 event6\n\n' \
    >> "${SANDBOX}/input-devices"
mkdir -p "${SANDBOX}/sys/class/input/event2/device/device" "${SANDBOX}/sys/class/input/event2/device"
printf 'A1B2C3D4E5\n' > "${SANDBOX}/sys/class/input/event2/device/serial"
out="$("$TOOL" status)"
check "exactly one pad listed" "1" "$(printf '%s\n' "$out" | grep -c '2dc8:310a XInput')"
check_absent "keyboard not a pad" "event3" "$out"
check_absent "mouse not a pad" "event6" "$out"
check_contains "serial from sysfs for xpad" "serial: A1B2C3D4E5" "$out"
check_absent "no pad count for a single pad" "pads — one mapping" "$out"
teardown

echo "argument handling"
setup
out="$("$TOOL" --version)"
check_contains "version" "bc250-paddles 0.1.0" "$out"
out="$("$TOOL" mapping)"
check "mapping prints exactly one line" "1" "$(printf '%s\n' "$out" | grep -c .)"
check_contains "mapping starts with the GUID" "${GUID},8BitDo Ultimate 2C Wireless Controller,crc:6fed," "$out"
"$TOOL" nonsens > /dev/null 2>&1
check "an unknown command fails" "1" "$?"
"$TOOL" --nonsens > /dev/null 2>&1
check "an unknown option fails" "1" "$?"
"$TOOL" on off > /dev/null 2>&1
check "two commands fail" "1" "$?"
teardown

echo "languages"
setup
install_native
out="$(BC250_LANG=pl "$TOOL" status)"
check_contains "Polish: state" "mapowanie wyłączone" "$out"
check_contains "Polish: heading" "Instalacje Steama" "$out"
out="$("$TOOL" --lang pl status)"
check_contains "--lang beats the variable" "mapowanie wyłączone" "$out"
out="$(BC250_LANG=de "$TOOL" status)"
check_contains "an unknown language falls back to English" "mapping off" "$out"
out="$(BC250_LANG=pl "$TOOL" --help)"
check_contains "help in Polish" "Użycie: bc250-paddles" "$out"
teardown

printf '\n%d passed, %d failed\n' "$passed" "$failed"
[ "$failed" -eq 0 ]
