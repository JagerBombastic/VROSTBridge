param([switch]$DependenciesReady)
$ErrorActionPreference='Stop'
$root=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
function Run-Checked {param([string]$Program,[string[]]$Arguments) & $Program @Arguments; if($LASTEXITCODE -ne 0){throw "$Program failed ($LASTEXITCODE)"} }
if ($env:OS -ne 'Windows_NT') {throw 'The host build needs Windows x64.'}
foreach($tool in @('git','cargo','rustup','cmake','python')) {
    if(-not(Get-Command $tool -ErrorAction SilentlyContinue)){throw "Missing $tool. Install the prerequisites listed in docs/BUILDING.md and reopen the launcher."}
}
# A double-click build must not require a pre-opened Visual Studio terminal.
if (-not(Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    $vswhere=Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    if (-not(Test-Path $vswhere)) {throw 'Install Visual Studio 2022 Build Tools with Desktop development with C++ and Windows SDK.'}
    $installation=& $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $installation) {throw 'Visual Studio C++ x64 tools were not found.'}
    Import-Module (Join-Path $installation 'Common7/Tools/Microsoft.VisualStudio.DevShell.dll')
    Enter-VsDevShell -VsInstallPath $installation -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64'
}
if (-not(Get-Command cl.exe -ErrorAction SilentlyContinue)) {throw 'MSVC environment could not be initialized.'}
$logDirectory=Join-Path $root 'build-evidence'
New-Item -ItemType Directory -Force $logDirectory | Out-Null
$log=Join-Path $logDirectory ('windows-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')
Start-Transcript -Path $log | Out-Null
try {
    Run-Checked 'rustup' @('toolchain','install','1.97.1','--profile','minimal')
    Run-Checked 'python' @((Join-Path $PSScriptRoot 'fetch_upstream.py'))
    Push-Location (Join-Path $root 'upstream/ALVR')
    try {
        if ($DependenciesReady) {
            foreach($tool in @('unzip','clang','pkg-config')) {
                if(-not(Get-Command $tool -ErrorAction SilentlyContinue)){throw "DependenciesReady requested, but $tool is missing."}
            }
            Run-Checked 'cargo' @('xtask','prepare-deps','--platform','windows','--ci')
        } else {
            if(-not(Get-Command choco -ErrorAction SilentlyContinue)){throw 'ALVR dependency preparation needs Chocolatey. Install it, or preinstall the dependencies and run with -DependenciesReady.'}
            Run-Checked 'cargo' @('xtask','prepare-deps','--platform','windows')
        }
        # Upstream uses setx; that does not refresh the current build process.
        $env:PKG_CONFIG_PATH=(Join-Path (Get-Location) 'deps')+';'+(Join-Path (Get-Location) 'deps/windows')+';'+$env:PKG_CONFIG_PATH
        $llvm=Join-Path $env:ProgramFiles 'LLVM/bin'
        if(Test-Path $llvm){$env:PATH=$llvm+';'+$env:PATH;$env:LIBCLANG_PATH=$llvm}
        Run-Checked 'cargo' @('xtask','build-streamer','--release','--keep-config')
        $source=Join-Path (Get-Location) 'build/alvr_streamer_windows'
        foreach($file in @('ALVR Dashboard.exe','driver.vrdrivermanifest','bin/win64/driver_alvr_server.dll')) {
            if(-not(Test-Path (Join-Path $source $file))){throw "Host build did not produce $file"}
        }
        $destination=Join-Path $root 'dist/host'
        New-Item -ItemType Directory -Force $destination | Out-Null
        # Preserve the user's session and trust list across rebuilds.
        Get-ChildItem $source | Where-Object {$_.Name -ne 'session.json'} | Copy-Item -Destination $destination -Recurse -Force
        # Record exactly which native sources were built. The release assembler
        # rejects an old DLL or a manifest from a different patch set.
        Run-Checked 'python' @((Join-Path $PSScriptRoot 'native_build_manifest.py'),
            '--dist',$destination,'--compiler','MSVC Visual Studio 2022; Rust 1.97.1',
            '--include','ALVR Dashboard.exe','--include','bin/win64/driver_alvr_server.dll')
    } finally {Pop-Location}
    Run-Checked 'cl.exe' @('/nologo','/std:c++17','/EHsc','/MT','/D_CRT_SECURE_NO_WARNINGS',('/I'+(Join-Path $root 'third_party/openvr')),(Join-Path $root 'host/vr_helper.cpp'),('/Fe:'+(Join-Path $root 'host/VROSTBridgeVRHelper.exe')),('/Fo:'+(Join-Path $logDirectory 'vr_helper.obj')))
    Run-Checked 'cl.exe' @('/nologo','/std:c++17','/EHsc','/MT',(Join-Path $root 'host/audio_probe.cpp'),('/Fe:'+(Join-Path $root 'host/VROSTBridgeAudioProbe.exe')),('/Fo:'+(Join-Path $logDirectory 'audio_probe.obj')),'ole32.lib','uuid.lib')
    Write-Output 'Windows host built. Open scripts\Start-Host.cmd, run readiness checks, save the profile and register the driver.'
} finally {Stop-Transcript | Out-Null}
