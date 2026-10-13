#!/usr/bin/env bash
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
message='Remove VROSTBridge? Any active session closes, and Steam restarts if VROSTBridge is in your Steam library. Your settings, paired client identity and logs are kept.'
if command -v kdialog >/dev/null; then
    kdialog --title VROSTBridge --yesno "$message" || exit 0
elif command -v zenity >/dev/null; then
    zenity --question --title VROSTBridge --text "$message" || exit 0
elif [[ "${VROSTBRIDGE_UNINSTALL_CONFIRMED:-0}" != 1 ]]; then
    printf '%s [y/N] ' "$message"
    read -r answer
    [[ "$answer" == y || "$answer" == Y ]] || exit 0
fi
# 1.0.0.3: Baballonia (the eye-tracking program VROSTBridge downloaded) is kept unless removing it is chosen, so that
# installing VROSTBridge again (for example an update after uninstalling) uses it without downloading it again.
data="${XDG_DATA_HOME:-$HOME/.local/share}"
keep=(--keep-eye-tracking)
kept=' Baballonia, the eye-tracking program, was kept for the next installation (in ~/.local/share/vrostbridge/baballonia).'
if [[ -d "$data/vrostbridge/baballonia" ]] && [[ -n "$(ls -A -- "$data/vrostbridge/baballonia" 2>/dev/null)" ]]; then
    # Keep is the default button; only Remove removes it; Cancel (or closing the question) cancels the uninstall.
    question='Keep Baballonia, the eye-tracking program VROSTBridge downloaded (about 840 MB)? Keep it if you will install VROSTBridge again: it is then used without downloading it again. Its settings and calibration stay either way.'
    choice=keep
    if command -v kdialog >/dev/null; then
        rc=0
        kdialog --title VROSTBridge --yes-label Keep --no-label Remove --cancel-label Cancel --yesnocancel "$question" || rc=$?
        case "$rc" in 0) choice=keep ;; 1) choice=remove ;; *) exit 0 ;; esac
    elif command -v zenity >/dev/null; then
        rc=0
        answer=$(zenity --question --title VROSTBridge --ok-label Keep --cancel-label Cancel --extra-button Remove --text "$question") || rc=$?
        if [[ "${answer:-}" == Remove ]]; then choice=remove; elif [[ "$rc" != 0 ]]; then exit 0; fi
    elif [[ "${VROSTBRIDGE_UNINSTALL_CONFIRMED:-0}" != 1 ]]; then
        printf '%s [K/r] ' "$question"
        read -r answer || answer=''
        if [[ "$answer" == r || "$answer" == R ]]; then choice=remove; fi
    elif [[ "${VROSTBRIDGE_KEEP_EYE_TRACKING:-1}" == 0 ]]; then
        choice=remove
    fi
    if [[ "$choice" == remove ]]; then keep=(); kept=''; fi
else
    kept=''
fi
result=0
if "$here/VROSTBridge-Client" --role client --installer-maintenance uninstall-client --target "$here" ${keep[@]+"${keep[@]}"}; then
    command -v update-desktop-database >/dev/null && update-desktop-database "$data/applications" || true
    message="VROSTBridge was removed, including its Steam library entry. Your settings, paired client identity and logs were kept.$kept"
else
    result=1
    message='VROSTBridge could not be removed safely. Close it from its web page and retry. See the client startup log for details.'
fi
if command -v kdialog >/dev/null; then kdialog --title VROSTBridge --msgbox "$message"
elif command -v zenity >/dev/null; then zenity --info --title VROSTBridge --text "$message"
else printf '%s\n' "$message"; fi

exit "$result"
