#!/usr/bin/env bash
# bc250-nosuspend tests against a stubbed systemctl — they never touch the real system.
set -uo pipefail

TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL="${TOOL_DIR}/bin/bc250-nosuspend"

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

# --- environment stub ------------------------------------------------------

setup() {
    SANDBOX="$(mktemp -d)"
    MASKED="${SANDBOX}/masked"
    : > "$MASKED"

    mkdir -p "${SANDBOX}/stub"
    cat > "${SANDBOX}/stub/systemctl" <<'STUB'
#!/usr/bin/env bash
# systemctl stub: it keeps the mask state in the file named by $MASKED.
case "$1" in
    is-enabled) grep -qxF "$2" "$MASKED" && { echo masked; exit 1; }; echo static; exit 1 ;;
    mask)       grep -qxF "$2" "$MASKED" || echo "$2" >> "$MASKED"; exit 0 ;;
    unmask)     grep -vxF "$2" "$MASKED" > "${MASKED}.tmp" || true
                mv "${MASKED}.tmp" "$MASKED"; exit 0 ;;
    reload)     exit 0 ;;
    --version)  echo "systemd 257 (atrapa)"; exit 0 ;;
esac
exit 0
STUB

    cat > "${SANDBOX}/stub/busctl" <<'STUB'
#!/usr/bin/env bash
# IdleAction depends on whether our drop-in exists.
if [ -f "${DROPIN_PROBE}" ]; then echo 's "ignore"'; else echo 's "suspend"'; fi
STUB
    chmod +x "${SANDBOX}/stub/systemctl" "${SANDBOX}/stub/busctl"

    export MASKED
    export DROPIN_PROBE="${SANDBOX}/etc/systemd/logind.conf.d/10-bc250-nosuspend.conf"
    export BC250_NOSUSPEND_SYSTEMCTL="${SANDBOX}/stub/systemctl"
    export BC250_NOSUSPEND_BUSCTL="${SANDBOX}/stub/busctl"
    export BC250_NOSUSPEND_ETC="${SANDBOX}/etc"
    export BC250_NOSUSPEND_STATE="${SANDBOX}/state"
    export BC250_NOSUSPEND_ASSUME_ROOT=1
    export NO_COLOR=1
    # the tests check message content, so the language must be explicit
    export BC250_LANG=pl
}

teardown() {
    rm -rf "$SANDBOX"
}

# grep -c on an empty file prints 0 and returns 1 — without capturing into a
# variable the fallback branch would append a second zero
masked_count() {
    local n
    n="$(grep -c . "$MASKED" 2>/dev/null)" || n=0
    printf '%s' "${n:-0}"
}

# --- tests ------------------------------------------------------------------

echo "no block in place"
setup
out="$("$TOOL" status)"; code=$?
check "status exits with code 1" "1" "$code"
check_contains "status says suspend is possible" "usypianie możliwe" "$out"
check_contains "IdleAction shown as suspend" "suspend — bezczynność" "$out"

echo "dry-run changes nothing"
out="$("$TOOL" start --dry-run)"
check "no target masked" "0" "$(masked_count)"
check "drop-in not created" "1" "$([ -f "$DROPIN_PROBE" ]; echo $?)"
check_contains "dry-run prints the planned masking" "mask sleep.target" "$out"
teardown

echo "start sets the block"
setup
"$TOOL" start > /dev/null
check "all five targets masked" "5" "$(masked_count)"
check "drop-in created" "0" "$([ -f "$DROPIN_PROBE" ]; echo $?)"
check_contains "drop-in sets IdleAction" "IdleAction=ignore" "$(cat "$DROPIN_PROBE")"
out="$("$TOOL" status)"; code=$?
check "status exits with code 0" "0" "$code"
check_contains "status reports the block" "SUSPEND ZABLOKOWANY" "$out"

echo "a repeated start is safe"
out="$("$TOOL" start)"
check_contains "detects the existing block" "już aktywna" "$out"
check "still five targets" "5" "$(masked_count)"

echo "stop removes the block"
"$TOOL" stop > /dev/null
check "everything unmasked" "0" "$(masked_count)"
check "drop-in removed" "1" "$([ -f "$DROPIN_PROBE" ]; echo $?)"
out="$("$TOOL" status)"; code=$?
check "status returns to code 1" "1" "$code"
teardown

echo "stop leaves masks set by somebody else alone"
setup
echo "hibernate.target" > "$MASKED"        # somebody masked it before us
"$TOOL" start > /dev/null
check "start topped it up to five" "5" "$(masked_count)"
"$TOOL" stop 2>/dev/null > /dev/null
check "the foreign mask is preserved" "1" "$(masked_count)"
check_contains "and it is hibernate.target" "hibernate.target" "$(cat "$MASKED")"

echo "default cleans everything up"
"$TOOL" default > /dev/null
check "no mask left" "0" "$(masked_count)"
check "drop-in removed" "1" "$([ -f "$DROPIN_PROBE" ]; echo $?)"
teardown

echo "argument handling"
setup
out="$("$TOOL" --version)"
check_contains "version" "bc250-nosuspend 0.1.0" "$out"
"$TOOL" nonsens > /dev/null 2>&1
check "an unknown command fails" "1" "$?"
"$TOOL" --nonsens > /dev/null 2>&1
check "an unknown option fails" "1" "$?"
out="$("$TOOL" 2>/dev/null)"; code=$?
check "with no arguments it behaves like status" "1" "$code"
teardown

echo "languages"
setup
out="$(BC250_LANG=en "$TOOL" status)"
check_contains "English: state" "suspend possible" "$out"
check_contains "English: targets heading" "systemd targets" "$out"
out="$(BC250_LANG=pl "$TOOL" status)"
check_contains "Polish: state" "usypianie możliwe" "$out"
check_contains "Polish: targets heading" "Cele systemd" "$out"
out="$("$TOOL" --lang en status)"
check_contains "--lang beats the variable" "suspend possible" "$out"
out="$(BC250_LANG=de "$TOOL" status)"
check_contains "an unknown language falls back to English" "suspend possible" "$out"
out="$(BC250_LANG=en "$TOOL" --help)"
check_contains "help in English" "Usage: bc250-nosuspend" "$out"
teardown

printf '\n%d passed, %d failed\n' "$passed" "$failed"
[ "$failed" -eq 0 ]
