Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
Name "VROSTBridge Host 1.0.0.13"
Caption "VROSTBridge Host 1.0.0.13 Setup"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\VROSTBridge\Host\1.0.0.13"
RequestExecutionLevel user
SetCompressor /SOLID zlib
ShowInstDetails show
BrandingText "VROSTBridge - an open-source remote PCVR bridge"
VIProductVersion "1.0.0.13"
VIAddVersionKey /LANG=1033 "ProductName" "VROSTBridge Host"
VIAddVersionKey /LANG=1033 "FileDescription" "VROSTBridge Host 1.0.0.13 Setup"
VIAddVersionKey /LANG=1033 "FileVersion" "1.0.0.13"
VIAddVersionKey /LANG=1033 "ProductVersion" "1.0.0.13"
VIAddVersionKey /LANG=1033 "LegalCopyright" "Copyright (c) 2026 JagerBombastic. MIT licence."
!define APPKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\VROSTBridgeHost"
!define RUNKEY "Software\Microsoft\Windows\CurrentVersion\Run"
!define LAUNCHER "VROSTBridge Host.exe"
!define MENU "$SMPROGRAMS\VROSTBridge"
!define MUI_ICON "${PAYLOAD}/brand/vrostbridge.ico"
!define MUI_UNICON "${PAYLOAD}/brand/vrostbridge.ico"
Var PreviousInstall
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "VROSTBridge Host 1.0.0.13"
!define MUI_WELCOMEPAGE_TEXT "Install the Windows rendering host for VROSTBridge.$\r$\n$\r$\nSteam and SteamVR must already be installed. Connect your physical headset and controllers to the Bazzite client PC.$\r$\n$\r$\nSetup verifies the new application, closes an existing session and replaces older application files. Settings, pairing and logs are retained. Close SteamVR on this Windows PC before continuing."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${LAUNCHER}"
!define MUI_FINISHPAGE_RUN_TEXT "Open VROSTBridge Host"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "VROSTBridge Host (required)" SEC_HOST
  SectionIn RO
  InitPluginsDir
  ; Stage and validate everything before stopping or replacing the old application.
  SetOutPath "$PLUGINSDIR\payload"
  File /r "${PAYLOAD}/*"
  ClearErrors
  ExecWait '"$PLUGINSDIR\payload\${LAUNCHER}" --self-test' $0
  ${If} ${Errors}
  ${OrIf} $0 != 0
    MessageBox MB_ICONSTOP "The new launcher failed its startup check. Your existing installation is unchanged. See $LOCALAPPDATA\VROSTBridge\host-native.log."
    Abort
  ${EndIf}
  nsExec::ExecToLog '"$PLUGINSDIR\payload\runtime\python\python.exe" -X utf8 "$PLUGINSDIR\payload\launcher\install_maintenance.py" prepare-host --target "$INSTDIR"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "The existing application could not be prepared safely. Close VROSTBridge Host and SteamVR, then retry. See the installation details for the reason."
    Abort
  ${EndIf}
  nsExec::ExecToLog '"$PLUGINSDIR\payload\runtime\python\python.exe" -X utf8 "$PLUGINSDIR\payload\launcher\install_maintenance.py" commit-host --target "$INSTDIR" --staged-payload "$PLUGINSDIR\payload"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "Application replacement failed. The previous version was retained or restored. See the installation details."
    Abort
  ${EndIf}
  nsExec::ExecToLog '"$PLUGINSDIR\payload\runtime\python\python.exe" -X utf8 "$PLUGINSDIR\payload\launcher\install_maintenance.py" finish-host --target "$INSTDIR"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "Upgrade migration could not finish. The old application files and settings backups were retained. Close SteamVR and rerun this installer; see the installation details for the reason."
    Abort
  ${EndIf}
  SetOutPath "$INSTDIR"
  CreateDirectory "${MENU}"
  CreateShortcut "${MENU}\VROSTBridge Host.lnk" "$INSTDIR\${LAUNCHER}"
  CreateShortcut "${MENU}\Open-source credits.lnk" "$INSTDIR\docs\OPEN_SOURCE_CREDITS.md"
  CreateShortcut "${MENU}\Startup logs.lnk" "$WINDIR\explorer.exe" '"$LOCALAPPDATA\VROSTBridge"'
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortcut "${MENU}\Uninstall VROSTBridge Host.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${APPKEY}" "DisplayName" "VROSTBridge Host"
  WriteRegStr HKCU "${APPKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${APPKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${APPKEY}" "DisplayVersion" "1.0.0.13"
  WriteRegStr HKCU "${APPKEY}" "Publisher" "JagerBombastic"
  WriteRegStr HKCU "${APPKEY}" "DisplayIcon" "$INSTDIR\${LAUNCHER}"
  WriteRegDWORD HKCU "${APPKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${APPKEY}" "NoRepair" 1
  ; Remove the exact previous owned command before adding an upgraded entry.
  ; Leaving the next section unchecked also disables an existing owned entry.
  ReadRegStr $0 HKCU "${RUNKEY}" "VROSTBridgeHost"
  ${If} $PreviousInstall != ""
  ${AndIf} $0 == '"$PreviousInstall\${LAUNCHER}" --background'
    DeleteRegValue HKCU "${RUNKEY}" "VROSTBridgeHost"
  ${EndIf}
SectionEnd
Section "Start in the system tray when I sign in" SEC_STARTUP
  ReadRegStr $0 HKCU "${RUNKEY}" "VROSTBridgeHost"
  ${If} $0 == ""
  ${OrIf} $0 == '"$INSTDIR\${LAUNCHER}" --background'
    WriteRegStr HKCU "${RUNKEY}" "VROSTBridgeHost" '"$INSTDIR\${LAUNCHER}" --background'
  ${Else}
    MessageBox MB_ICONEXCLAMATION "The sign-in startup entry (VROSTBridgeHost) already contains a different command and was left unchanged. Open VROSTBridge Host before starting a client session."
  ${EndIf}
SectionEnd
Section "Microsoft C++ runtime (recommended)" SEC_VC
  DetailPrint "Opening Microsoft's bundled C++ runtime installer. Windows may request administrator approval."
  ClearErrors
  ExecShellWait "open" "$INSTDIR\runtime\vc_redist.x64.exe" "/install /passive /norestart"
  ${If} ${Errors}
    MessageBox MB_ICONEXCLAMATION "The Microsoft C++ installer did not open. VROSTBridge Host is installed; use runtime\vc_redist.x64.exe in its folder before starting ALVR if Check this PC reports missing runtime DLLs."
  ${EndIf}
SectionEnd
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_HOST} "Desktop launcher, matching ALVR host, bundled Python, documentation and licences. Reinstall repairs files; upgrades preserve pairing, settings and logs."
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_STARTUP} "Keep the host companion ready after Windows sign-in without opening a browser or SteamVR. Start and stop the VR session from the client. The host PC must remain awake and signed in."
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_VC} "Install or repair Microsoft's signed x64 C++ runtime required by the ALVR host. Its own installer reports errors or a required restart."
!insertmacro MUI_FUNCTION_DESCRIPTION_END
Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "VROSTBridge Host requires 64-bit Windows 10 or newer."
    Abort
  ${EndIf}
  SetShellVarContext current
  ReadRegStr $PreviousInstall HKCU "${APPKEY}" "InstallLocation"
  ${If} $PreviousInstall != ""
    ReadRegStr $0 HKCU "${RUNKEY}" "VROSTBridgeHost"
    ${If} $0 != '"$PreviousInstall\${LAUNCHER}" --background'
      SectionSetFlags ${SEC_STARTUP} 0
    ${EndIf}
  ${EndIf}
FunctionEnd
Section "Uninstall"
  SetShellVarContext current
  MessageBox MB_OKCANCEL "Remove VROSTBridge Host? Close SteamVR on this Windows PC first. Your settings, pairing and logs will be kept." IDOK +2
  Abort
  nsExec::ExecToLog '"$INSTDIR\runtime\python\python.exe" -X utf8 "$INSTDIR\launcher\install_maintenance.py" uninstall-host --target "$INSTDIR"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "VROSTBridge Host could not be removed safely. Close SteamVR and VROSTBridge Host, then retry. See the uninstaller details."
    Abort
  ${EndIf}
  ; A stale uninstaller must not remove shortcuts or the entry for a newer install.
  ReadRegStr $0 HKCU "${APPKEY}" "InstallLocation"
  ${If} $0 == $INSTDIR
    ReadRegStr $1 HKCU "${RUNKEY}" "VROSTBridgeHost"
    ${If} $1 == '"$INSTDIR\${LAUNCHER}" --background'
      DeleteRegValue HKCU "${RUNKEY}" "VROSTBridgeHost"
    ${EndIf}
    Delete "${MENU}\VROSTBridge Host.lnk"
    Delete "${MENU}\Uninstall VROSTBridge Host.lnk"
    Delete "${MENU}\Open-source credits.lnk"
    Delete "${MENU}\Startup logs.lnk"
    RMDir "${MENU}"
    DeleteRegKey HKCU "${APPKEY}"
  ${EndIf}
  SetOutPath "$TEMP"
  RMDir /r "$INSTDIR"
SectionEnd
