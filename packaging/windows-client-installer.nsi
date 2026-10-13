; VROSTBridge Client for Windows 10 and 11 (x64): a per-user installer, modelled on the host's (windows-installer.nsi).
; Built by packaging/build_windows_client.py, which passes the payload, the output and the version from
; launcher/version.py: makensis -DPAYLOAD=<payload> -DOUTPUT=<setup.exe> -DVERSION=<version> -DVERSION_NUMERIC=<a.b.c.d>
; The client sits beside VROSTBridge Host on one PC: its own folder (Client\<version>), uninstall key
; (VROSTBridgeClient), sign-in value (VROSTBridgeClient) and Start menu shortcuts in the shared VROSTBridge folder.
; Silent use: setup /S [/STARTUP] (sign-in startup is off unless asked for); Uninstall.exe /S [/REMOVEEYETRACKING]
; (the downloaded Baballonia is kept unless asked).
Unicode true
!ifndef PAYLOAD
  !error "Build with packaging/build_windows_client.py (it passes PAYLOAD, OUTPUT, VERSION and VERSION_NUMERIC)."
!endif
!ifndef OUTPUT
  !error "Build with packaging/build_windows_client.py (it passes PAYLOAD, OUTPUT, VERSION and VERSION_NUMERIC)."
!endif
!ifndef VERSION
  !error "Build with packaging/build_windows_client.py (it passes PAYLOAD, OUTPUT, VERSION and VERSION_NUMERIC)."
!endif
!ifndef VERSION_NUMERIC
  !error "Build with packaging/build_windows_client.py (it passes PAYLOAD, OUTPUT, VERSION and VERSION_NUMERIC)."
!endif
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!include "FileFunc.nsh"
!include "Sections.nsh"
Name "VROSTBridge Client ${VERSION}"
Caption "VROSTBridge Client ${VERSION} Setup"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\VROSTBridge\Client\${VERSION}"
RequestExecutionLevel user
ManifestSupportedOS all
SetCompressor /SOLID zlib
ShowInstDetails show
BrandingText "VROSTBridge - an open-source remote PCVR bridge"
VIProductVersion "${VERSION_NUMERIC}"
VIAddVersionKey /LANG=1033 "ProductName" "VROSTBridge Client"
VIAddVersionKey /LANG=1033 "FileDescription" "VROSTBridge Client ${VERSION} Setup"
VIAddVersionKey /LANG=1033 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "LegalCopyright" "Copyright (c) 2026 JagerBombastic. MIT licence."
!define APPKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\VROSTBridgeClient"
!define RUNKEY "Software\Microsoft\Windows\CurrentVersion\Run"
!define RUNVALUE "VROSTBridgeClient"
!define LAUNCHER "VROSTBridge Client.exe"
!define MENU "$SMPROGRAMS\VROSTBridge"
; Where the companion downloads Baballonia, the optional eye-tracking program (launcher/baballonia.py).
!define EYE "$LOCALAPPDATA\VROSTBridge\Baballonia"
!define PYTHON '"$PLUGINSDIR\payload\runtime\python\python.exe" -X utf8 "$PLUGINSDIR\payload\launcher\install_maintenance.py"'
!define MUI_ICON "${PAYLOAD}/brand/vrostbridge.ico"
!define MUI_UNICON "${PAYLOAD}/brand/vrostbridge.ico"
Var PreviousInstall
Var EyeTracking
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "VROSTBridge Client ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "Install the headset PC's client for VROSTBridge.$\r$\n$\r$\nSteam and SteamVR must already be installed on this PC, with the headset and controllers connected to it. The rendering PC runs VROSTBridge Host.$\r$\n$\r$\nSetup verifies the new application, closes an open VROSTBridge Client and replaces older application files. Settings, pairing and logs are kept. Close SteamVR on this PC before continuing."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${LAUNCHER}"
!define MUI_FINISHPAGE_RUN_TEXT "Open VROSTBridge Client"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "VROSTBridge Client (required)" SEC_CLIENT
  SectionIn RO
  InitPluginsDir
  ; Stage and validate everything before stopping or replacing the old application.
  SetOutPath "$PLUGINSDIR\payload"
  File /r "${PAYLOAD}/*"
  ClearErrors
  ExecWait '"$PLUGINSDIR\payload\${LAUNCHER}" --self-test' $0
  ${If} ${Errors}
  ${OrIf} $0 != 0
    MessageBox MB_ICONSTOP "The new launcher failed its startup check. Your existing installation is unchanged. See $LOCALAPPDATA\VROSTBridge\client-native.log." /SD IDOK
    Abort
  ${EndIf}
  nsExec::ExecToLog '${PYTHON} prepare-windows-client --target "$INSTDIR"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "The existing application could not be prepared safely. Close VROSTBridge Client and SteamVR, then retry. See the installation details for the reason." /SD IDOK
    Abort
  ${EndIf}
  nsExec::ExecToLog '${PYTHON} commit-windows-client --target "$INSTDIR" --staged-payload "$PLUGINSDIR\payload"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "Application replacement failed. The previous version was retained or restored. See the installation details." /SD IDOK
    Abort
  ${EndIf}
  nsExec::ExecToLog '${PYTHON} finish-windows-client --target "$INSTDIR"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "The update could not remove the earlier version. Close VROSTBridge Client and SteamVR, then rerun this installer; see the installation details for the reason." /SD IDOK
    Abort
  ${EndIf}
  SetOutPath "$INSTDIR"
  ; The host's shortcuts share this folder: every name here says Client.
  CreateDirectory "${MENU}"
  CreateShortcut "${MENU}\VROSTBridge Client.lnk" "$INSTDIR\${LAUNCHER}"
  CreateShortcut "${MENU}\Client open-source credits.lnk" "$INSTDIR\docs\OPEN_SOURCE_CREDITS.md"
  CreateShortcut "${MENU}\Client startup logs.lnk" "$WINDIR\explorer.exe" '"$LOCALAPPDATA\VROSTBridge"'
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortcut "${MENU}\Uninstall VROSTBridge Client.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${APPKEY}" "DisplayName" "VROSTBridge Client"
  WriteRegStr HKCU "${APPKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${APPKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${APPKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${APPKEY}" "Publisher" "JagerBombastic"
  WriteRegStr HKCU "${APPKEY}" "DisplayIcon" "$INSTDIR\${LAUNCHER}"
  WriteRegDWORD HKCU "${APPKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${APPKEY}" "NoRepair" 1
  ; Remove the exact previous owned command before adding an upgraded entry.
  ; Leaving the next section unchecked also disables an existing owned entry.
  ReadRegStr $0 HKCU "${RUNKEY}" "${RUNVALUE}"
  ${If} $PreviousInstall != ""
  ${AndIf} $0 == '"$PreviousInstall\${LAUNCHER}" --background'
    DeleteRegValue HKCU "${RUNKEY}" "${RUNVALUE}"
  ${EndIf}
SectionEnd
Section /o "Start when I sign in" SEC_STARTUP
  ReadRegStr $0 HKCU "${RUNKEY}" "${RUNVALUE}"
  ${If} $0 == ""
  ${OrIf} $0 == '"$INSTDIR\${LAUNCHER}" --background'
    WriteRegStr HKCU "${RUNKEY}" "${RUNVALUE}" '"$INSTDIR\${LAUNCHER}" --background'
  ${Else}
    MessageBox MB_ICONEXCLAMATION "The sign-in startup entry (VROSTBridgeClient) already contains a different command and was left unchanged. Open VROSTBridge Client from the Start menu." /SD IDOK
  ${EndIf}
SectionEnd
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_CLIENT} "Desktop launcher, native streaming client, bundled Python, documentation and licences. Reinstall repairs files; updates keep pairing, settings and logs."
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_STARTUP} "Start the client companion quietly when you sign in to Windows, without opening its page. Off unless chosen: open VROSTBridge Client when you want to play."
!insertmacro MUI_FUNCTION_DESCRIPTION_END
Function .onInit
  ${IfNot} ${RunningX64}
  ${OrIfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP "VROSTBridge Client requires 64-bit Windows 10 or newer." /SD IDOK
    Abort
  ${EndIf}
  SetShellVarContext current
  ; Sign-in startup stays as it was: on after a repair or an update only when the entry is this client's own
  ; command, never taking over another program's entry. A new installation starts it only when asked (/STARTUP).
  ReadRegStr $PreviousInstall HKCU "${APPKEY}" "InstallLocation"
  ${If} $PreviousInstall != ""
    ReadRegStr $0 HKCU "${RUNKEY}" "${RUNVALUE}"
    ${If} $0 == '"$PreviousInstall\${LAUNCHER}" --background'
      SectionSetFlags ${SEC_STARTUP} ${SF_SELECTED}
    ${EndIf}
  ${EndIf}
  ${GetParameters} $1
  ClearErrors
  ${GetOptions} $1 "/STARTUP" $2
  ${IfNot} ${Errors}
    SectionSetFlags ${SEC_STARTUP} ${SF_SELECTED}
  ${EndIf}
FunctionEnd

Function un.onInit
  SetShellVarContext current
  ; Silent choices for the downloaded Baballonia: /REMOVEEYETRACKING removes it, /KEEPEYETRACKING (the default) keeps it.
  StrCpy $EyeTracking ""
  ${un.GetParameters} $1
  ClearErrors
  ${un.GetOptions} $1 "/REMOVEEYETRACKING" $2
  ${IfNot} ${Errors}
    StrCpy $EyeTracking "remove"
  ${EndIf}
  ClearErrors
  ${un.GetOptions} $1 "/KEEPEYETRACKING" $2
  ${IfNot} ${Errors}
    StrCpy $EyeTracking "keep"
  ${EndIf}
FunctionEnd
Function un.EyeTrackingFound
  ; $R1 = 1 when the Baballonia folder has anything in it (as the Bazzite uninstaller asks).
  StrCpy $R1 0
  FindFirst $R0 $R2 "${EYE}\*"
  ${DoWhile} $R2 != ""
    ${If} $R2 != "."
    ${AndIf} $R2 != ".."
      StrCpy $R1 1
      ${ExitDo}
    ${EndIf}
    FindNext $R0 $R2
  ${Loop}
  FindClose $R0
FunctionEnd
Section "Uninstall"
  SetShellVarContext current
  MessageBox MB_OKCANCEL "Remove VROSTBridge Client? Close SteamVR on this PC first. Your settings, pairing and logs will be kept." /SD IDOK IDOK +2
  Abort
  ; Baballonia, the eye-tracking program the client downloads on request, is kept unless removing it is chosen, so
  ; that installing VROSTBridge Client again uses it without downloading it again. Its settings stay either way.
  ${If} $EyeTracking == ""
    StrCpy $EyeTracking "keep"
    Call un.EyeTrackingFound
    ${If} $R1 == 1
      MessageBox MB_YESNOCANCEL|MB_ICONQUESTION "Keep Baballonia, the eye-tracking program VROSTBridge downloaded (about 880 MB)? Keep it if you will install VROSTBridge Client again: it is then used without downloading it again. Its settings and calibration stay either way.$\r$\n$\r$\nYes keeps it, No removes it, Cancel leaves VROSTBridge Client installed." /SD IDYES IDYES eye_keep IDNO eye_remove
      Abort
      eye_remove:
      StrCpy $EyeTracking "remove"
      eye_keep:
    ${EndIf}
  ${EndIf}
  StrCpy $2 ""
  ${If} $EyeTracking == "keep"
    StrCpy $2 " --keep-eye-tracking"
  ${EndIf}
  ; Closes an open client companion, restores SteamVR settings a session changed, removes the client's own firewall
  ; rules (Windows asks for administrator permission once if there are any) and, if chosen, Baballonia.
  nsExec::ExecToLog '"$INSTDIR\runtime\python\python.exe" -X utf8 "$INSTDIR\launcher\install_maintenance.py" uninstall-windows-client --target "$INSTDIR"$2'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "VROSTBridge Client could not be removed safely. Close VROSTBridge Client and SteamVR, then retry, and approve Windows' request to remove the client's firewall rules if it asks. See the uninstaller details." /SD IDOK
    Abort
  ${EndIf}
  ; A stale uninstaller must not remove shortcuts or the entry for a newer install.
  ReadRegStr $0 HKCU "${APPKEY}" "InstallLocation"
  ${If} $0 == $INSTDIR
    ReadRegStr $1 HKCU "${RUNKEY}" "${RUNVALUE}"
    ${If} $1 == '"$INSTDIR\${LAUNCHER}" --background'
      DeleteRegValue HKCU "${RUNKEY}" "${RUNVALUE}"
    ${EndIf}
    Delete "${MENU}\VROSTBridge Client.lnk"
    Delete "${MENU}\Uninstall VROSTBridge Client.lnk"
    Delete "${MENU}\Client open-source credits.lnk"
    Delete "${MENU}\Client startup logs.lnk"
    RMDir "${MENU}"
    DeleteRegKey HKCU "${APPKEY}"
  ${EndIf}
  SetOutPath "$TEMP"
  RMDir /r "$INSTDIR"
  ; The versions folder goes when it is empty; the state folder (settings, pairing, logs) stays.
  RMDir "$LOCALAPPDATA\VROSTBridge\Client"
SectionEnd
