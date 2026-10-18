#!/usr/bin/env bash
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
base="$data/vrostbridge"
applications="$data/applications"
version=1.0.0.14
say() {
    if command -v kdialog >/dev/null; then kdialog --title VROSTBridge --msgbox "$1"
    elif command -v zenity >/dev/null; then zenity --info --title VROSTBridge --text "$1"
    else printf '%s\n' "$1"; fi
}
fail() { say "Installation failed: $1"; exit 1; }
[[ "$(uname -m)" == x86_64 ]] || fail 'This package needs an x86-64 PC.'
[[ -f "$here/payload/VROSTBridge-Client" ]] || fail 'Extract the whole installer ZIP before opening Install VROSTBridge.'
# The launcher's strict manifest verification below also rejects path traversal.
(cd "$here/payload" && sha256sum --quiet -c SHA256SUMS) || fail 'Package integrity check failed. Download and extract it again.'
identity=$(sha256sum "$here/payload/SHA256SUMS");identity=${identity%% *}
release="$base/versions/$version-${identity:0:12}"
mkdir -p -- "$base/versions" "$applications"
stage=$(mktemp -d "$base/versions/.install-XXXXXXXX")
replaced=''
committed=0
cleanup() {
    [[ -z "${stage:-}" ]] || rm -rf -- "$stage"
    if [[ -n "$replaced" && -d "$replaced" ]]; then
        if [[ "$committed" == 1 ]]; then rm -rf -- "$replaced"
        elif [[ ! -e "$release" ]]; then mv -- "$replaced" "$release"; fi
    fi
}
trap cleanup EXIT
cp -a -- "$here/payload/." "$stage/"
chmod +x "$stage/VROSTBridge-Client" "$stage/VROSTBridgeClient" "$stage/VROSTBridgeLease" "$stage/VROSTBridgeRuntimeProbe" "$stage/tools/go-bsb-cams" "$stage/uninstall.sh"
"$stage/VROSTBridge-Client" --role client --self-test || fail 'The bundled launcher could not load on this system. Your existing installation is unchanged.'
"$stage/VROSTBridge-Client" --role client --installer-maintenance prepare-client --target "$release" || fail 'The old application could not be stopped safely. Close it from its web page and retry.'
if [[ -e "$release" || -L "$release" ]]; then
    replaced=$(mktemp -d "$base/versions/.replaced-XXXXXXXX")
    rmdir -- "$replaced"
    mv -- "$release" "$replaced"
fi
mv -- "$stage" "$release"
stage=''
link="$base/.current-$$"
ln -s -- "$release" "$link"
mv -Tf -- "$link" "$base/current"
committed=1
# Quote desktop Exec paths according to the desktop entry specification.
escaped=${base//\\/\\\\};escaped=${escaped//\"/\\\"};escaped=${escaped//\$/\\\$};escaped=${escaped//\`/\\\`};escaped=${escaped//%/%%}
escaped=${escaped//\\/\\\\}
cat > "$applications/io.vrostbridge.Client.desktop" <<EOF
[Desktop Entry]
Type=Application
X-VROSTBridge-Managed=true
Name=VROSTBridge Client
GenericName=Remote PCVR bridge
Comment=Connect this PC's SteamVR headset to your rendering PC
Exec="$escaped/current/VROSTBridge-Client" --role client
Icon=vrostbridge
Terminal=false
Categories=Game;
StartupNotify=false
EOF
cat > "$applications/io.vrostbridge.Uninstall.desktop" <<EOF
[Desktop Entry]
Type=Application
X-VROSTBridge-Managed=true
Name=Uninstall VROSTBridge Client
Comment=Remove VROSTBridge while keeping settings and logs
Exec="$escaped/current/uninstall.sh"
Icon=vrostbridge
Terminal=false
Categories=System;
StartupNotify=false
EOF
cat > "$applications/io.vrostbridge.GameMode.desktop" <<EOF
[Desktop Entry]
Type=Application
X-VROSTBridge-Managed=true
Name=VROSTBridge Game Mode
Comment=Controller-friendly VROSTBridge controls for Steam Game Mode
Exec="$escaped/current/VROSTBridge-Client" --role client --game-mode
Icon=vrostbridge
Terminal=false
Categories=Game;
StartupNotify=false
EOF
# The VROSTBridge icon in every size it has (icon theme name: vrostbridge).
for icon in "$base/current/brand/hicolor"/*/apps/vrostbridge.*; do
    size=$(basename -- "$(dirname -- "$(dirname -- "$icon")")")
    mkdir -p -- "$data/icons/hicolor/$size/apps"
    cp -- "$icon" "$data/icons/hicolor/$size/apps/"
done
touch -- "$data/icons/hicolor" 2>/dev/null || true
"$base/current/VROSTBridge-Client" --role client --installer-maintenance finish-client --target "$release" || fail 'The new client is installed, but cleanup of an old version failed. No settings were removed.'
command -v update-desktop-database >/dev/null && update-desktop-database "$applications" || true
# 1.0.0.3: say whether the eye tracking set up before (Baballonia and its calibration) was found and kept.
eye=$("$base/current/VROSTBridge-Client" --role client --installer-maintenance eye-status --target "$release" 2>/dev/null | head -n 1 || true)
say "VROSTBridge is installed. Open VROSTBridge Client to configure or start a session. For Steam Game Mode, select Add to Steam on its page: VROSTBridge appears in your library with its artwork. Previous application files were removed; your settings, paired identity, calibration and logs were kept.${eye:+ $eye}"
if [[ "${VROSTBRIDGE_INSTALL_NO_LAUNCH:-0}" != 1 ]]; then
    state="${XDG_STATE_HOME:-$HOME/.local/state}/VROSTBridge"
    mkdir -p "$state"
    nohup "$base/current/VROSTBridge-Client" --role client >>"$state/launcher.log" 2>&1 &
fi
