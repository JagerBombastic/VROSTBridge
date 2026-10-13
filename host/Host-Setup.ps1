param([ValidateSet('Register','Unregister','Firewall','RemoveFirewall')][string]$Action='Register', [string]$HostDirectory)
$ErrorActionPreference='Stop'
if (-not $HostDirectory) { $HostDirectory=Join-Path $PSScriptRoot '../dist/host' }
$HostDirectory=(Resolve-Path $HostDirectory).Path
if (-not (Test-Path (Join-Path $HostDirectory 'driver.vrdrivermanifest'))) {throw 'Matching host driver files are missing. Repair or reinstall VROSTBridge Host.'}
# The rule names and the ownership marker identify the rules VROSTBridge created, so only those are ever removed.
$ruleNames=@('VROSTBridge.VR.TCP','VROSTBridge.VR.UDP','VROSTBridge.VR.mDNS','VROSTBridge.Session.TCP','VROSTBridge.Session.UDP')
$ownership='VROSTBridge private LAN streaming and discovery'
if ($Action -eq 'RemoveFirewall') {
    $ownedRules=@()
    foreach($name in $ruleNames) {
        $rule=Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
        if (-not $rule) {continue}
        if ($rule.Description -ne $ownership) {throw "Firewall rule $name does not carry the VROSTBridge ownership marker; it was not removed."}
        $ownedRules+=@($rule)
    }
    if (-not $ownedRules.Count) {Write-Output 'No VROSTBridge firewall rules remain.';exit}
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    $principal=New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $scriptEscaped=$PSCommandPath.Replace("'","''")
        $hostEscaped=$HostDirectory.Replace("'","''")
        $command="& '$scriptEscaped' -Action RemoveFirewall -HostDirectory '$hostEscaped'"
        $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        $elevated=Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',$encoded)
        if ($elevated.ExitCode -ne 0) {throw 'VROSTBridge firewall cleanup failed or permission was declined.'}
        Write-Output 'VROSTBridge firewall cleanup completed.'
        exit
    }
    $ownedRules | Remove-NetFirewallRule
    Write-Output 'Removed only the firewall rules created by VROSTBridge (named VROSTBridge.*).'
    exit
}
if ($Action -ne 'Firewall' -and (Get-Process vrserver -ErrorAction SilentlyContinue)) {throw 'Close SteamVR before changing driver registration.'}
$paths=Join-Path $env:LOCALAPPDATA 'openvr/openvrpaths.vrpath'
$candidates=New-Object 'System.Collections.Generic.List[string]'
# SteamVR's own runtime registry is authoritative, including other Steam libraries.
if (Test-Path $paths) {
    try {
        $registered=Get-Content $paths -Raw | ConvertFrom-Json
        foreach ($runtime in @($registered.runtime)) {
            if ($runtime) {$candidates.Add((Join-Path $runtime 'bin/win64/vrpathreg.exe'))}
        }
    } catch {Write-Warning 'SteamVR runtime registry could not be read. Trying Steam installation paths.'}
}
foreach ($key in @('HKCU:\Software\Valve\Steam','HKLM:\SOFTWARE\WOW6432Node\Valve\Steam')) {
    $settings=Get-ItemProperty $key -ErrorAction SilentlyContinue
    $steam=$settings.SteamPath
    if (-not $steam) {$steam=$settings.InstallPath}
    if ($steam) {$candidates.Add((Join-Path $steam 'steamapps/common/SteamVR/bin/win64/vrpathreg.exe'))}
}
$vr=$candidates | Where-Object {Test-Path $_ -PathType Leaf} | Select-Object -First 1
if (-not $vr) {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog=New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title='Locate SteamVR bin\win64\vrpathreg.exe';$dialog.Filter='SteamVR path registry|vrpathreg.exe'
    if ($dialog.ShowDialog() -ne 'OK') {throw 'SteamVR location was not selected.'}
    $vr=$dialog.FileName
}
if ($Action -eq 'Firewall') {
    $program=Join-Path (Split-Path $vr) 'vrserver.exe'
    if (-not (Test-Path $program -PathType Leaf)) {throw 'SteamVR server executable is missing.'}
    $sessionProgram=Join-Path $PSScriptRoot '../runtime/python/python.exe'
    if (-not (Test-Path $sessionProgram -PathType Leaf)) {throw 'The bundled host Python runtime is missing. Repair or reinstall VROSTBridge Host before enabling session discovery.'}
    $sessionProgram=(Resolve-Path $sessionProgram).Path
    # Session control is served by the companion's bundled Python, not vrserver.
    # Keep both programs confined to Private networks and directly attached LANs.
    $rules=@(
        @{Name='VROSTBridge.VR.TCP';Protocol='TCP';Ports=@(9943,9944);Program=$program},
        @{Name='VROSTBridge.VR.UDP';Protocol='UDP';Ports=@(9943,9944);Program=$program},
        @{Name='VROSTBridge.VR.mDNS';Protocol='UDP';Ports=@(5353);Program=$program},
        @{Name='VROSTBridge.Session.TCP';Protocol='TCP';Ports=@(9946);Program=$sessionProgram},
        @{Name='VROSTBridge.Session.UDP';Protocol='UDP';Ports=@(9946);Program=$sessionProgram}
    )
    $complete=$true
    foreach($expected in $rules) {
        $rule=Get-NetFirewallRule -Name $expected.Name -ErrorAction SilentlyContinue
        if ($rule -and $rule.Description -ne $ownership) {throw "Firewall rule $($expected.Name) already exists without the VROSTBridge ownership marker. It was not changed."}
        if (-not $rule -or $rule.Enabled -ne 'True' -or $rule.Action -ne 'Allow' -or $rule.Direction -ne 'Inbound' -or $rule.Profile -ne 'Private') {$complete=$false;continue}
        $ports=$rule | Get-NetFirewallPortFilter
        $addresses=$rule | Get-NetFirewallAddressFilter
        $application=$rule | Get-NetFirewallApplicationFilter
        if ($ports.Protocol -ne $expected.Protocol -or (@($ports.LocalPort) -join ',') -ne (@($expected.Ports) -join ',') -or (@($addresses.RemoteAddress) -join ',') -ne 'LocalSubnet' -or $application.Program -ne $expected.Program) {$complete=$false}
    }
    if ($complete) {Write-Output 'Private-LAN firewall rules are already configured.';exit}
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    $principal=New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        # EncodedCommand avoids quoting mistakes for spaces, Unicode and apostrophes.
        $scriptEscaped=$PSCommandPath.Replace("'","''")
        $hostEscaped=$HostDirectory.Replace("'","''")
        $command="& '$scriptEscaped' -Action Firewall -HostDirectory '$hostEscaped'"
        $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        $elevated=Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',$encoded)
        if ($elevated.ExitCode -ne 0) {throw 'Windows firewall setup failed or permission was declined.'}
        Write-Output 'Private-LAN firewall setup completed.'
        exit
    }
    foreach($expected in $rules) {
        $rule=Get-NetFirewallRule -Name $expected.Name -ErrorAction SilentlyContinue
        # Check again immediately before replacement, including after elevation.
        if ($rule -and $rule.Description -ne $ownership) {throw "Firewall rule $($expected.Name) is not owned by VROSTBridge; it was not changed."}
        if ($rule) {$rule | Remove-NetFirewallRule}
        New-NetFirewallRule -Name $expected.Name -DisplayName "$($expected.Name) (private LAN)" -Description $ownership -Program $expected.Program -Direction Inbound -Action Allow -Protocol $expected.Protocol -LocalPort $expected.Ports -Profile Private -RemoteAddress LocalSubnet | Out-Null
    }
    Write-Output 'Private-LAN rules configured: SteamVR streaming 9943/9944, mDNS UDP 5353 and companion session discovery/control TCP/UDP 9946. The desktop web UI stays on loopback.'
    exit
}
$state=Join-Path $HostDirectory 'vrostbridge-registration.json'
if ($Action -eq 'Register') {
    $existing=@()
    if (Test-Path $paths) { $existing=@((Get-Content $paths -Raw | ConvertFrom-Json).external_drivers) }
    $already=$existing | Where-Object { $_ -and [IO.Path]::GetFullPath($_).TrimEnd('\','/') -eq $HostDirectory.TrimEnd('\','/') }
    $name=(Get-Content (Join-Path $HostDirectory 'driver.vrdrivermanifest') -Raw | ConvertFrom-Json).name
    $owned=New-Object 'System.Collections.Generic.List[string]'
    $oldBase=[IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'VROSTBridge')).TrimEnd('\','/')+'\'
    foreach ($entry in $existing) {
        if (-not $entry) {continue}
        $full=[IO.Path]::GetFullPath($entry).TrimEnd('\','/')
        if ($full -eq $HostDirectory.TrimEnd('\','/')) {continue}
        $manifest=Join-Path $full 'driver.vrdrivermanifest'
        if (-not (Test-Path $manifest)) {continue}
        try {$other=(Get-Content $manifest -Raw | ConvertFrom-Json).name}
        catch {throw "Another registered driver's manifest is unreadable: $manifest. Fix that registration before continuing."}
        if ($other -ne $name) {continue}
        $isOwned=$false
        if ($full.StartsWith($oldBase,[StringComparison]::OrdinalIgnoreCase)) {
            $ancestor=Get-Item $full
            while ($ancestor -and $ancestor.FullName.StartsWith($oldBase,[StringComparison]::OrdinalIgnoreCase)) {
                $identity=Join-Path $ancestor.FullName 'PACKAGED.json'
                if (Test-Path $identity) {
                    try {
                        $package=Get-Content $identity -Raw | ConvertFrom-Json
                        $isOwned=$package.role -eq 'host' -and $package.alvr_commit -eq 'f5023f8e78fa14ea57fb1038548d7820289a8524'
                    } catch {$isOwned=$false}
                    break
                }
                $ancestor=$ancestor.Parent
            }
        }
        if (-not $isOwned) {
            throw "Another ALVR host is registered at $full. Open that ALVR installation and unregister its driver before registering VROSTBridge. No driver registrations were changed."
        }
        $owned.Add($full)
    }
    if (-not $already -or $owned.Count) {
        $backup=$null
        if (Test-Path $paths) {
            $backup=Join-Path $HostDirectory ('openvrpaths.before.'+[DateTime]::UtcNow.Ticks+'.json')
            Copy-Item $paths $backup
        }
        try {
            foreach ($previous in $owned) {
                & $vr removedriver $previous
                if ($LASTEXITCODE -ne 0) {throw "Could not remove the previous VROSTBridge registration at $previous."}
            }
            if (-not $already) {
                & $vr adddriver $HostDirectory
                if ($LASTEXITCODE -ne 0) {throw 'SteamVR driver registration failed.'}
                @{added=$true;path=$HostDirectory;vrpathreg=$vr;migrated=$owned.ToArray()} | ConvertTo-Json | Set-Content $state
            }
        } catch {
            $failure=$_
            if ($backup) {Copy-Item $backup $paths -Force}
            elseif (-not $already) {& $vr removedriver $HostDirectory | Out-Null}
            throw "Registration failed and its changes were rolled back: $failure"
        }
        foreach ($previous in $owned) {Write-Output "Replaced previous VROSTBridge driver registration: $previous"}
    }
    Write-Output 'Host driver registered. Physical client drivers are unchanged.'
} else {
    # The exact user-selected package directory is the only registration removed.
    # Its old registration receipt may be absent after an interrupted upgrade.
    $existing=@()
    if (Test-Path $paths) {$existing=@((Get-Content $paths -Raw | ConvertFrom-Json).external_drivers)}
    $registered=$existing | Where-Object {$_ -and [IO.Path]::GetFullPath($_).TrimEnd('\','/') -eq $HostDirectory.TrimEnd('\','/')}
    if ($registered) {
        & $vr removedriver $HostDirectory
        if ($LASTEXITCODE -ne 0) {throw 'Driver removal failed.'}
    }
    if (Test-Path $state) {Remove-Item $state}
}
