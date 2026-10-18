# VROSTBridge client: Windows Firewall rules that let the rendering host reach this headset PC on the
# home network (local subnet, any network type from 1.0.0.14). Modelled on host/Host-Setup.ps1 -Action Firewall; launcher/client_network.py runs it.
#   -Action Check           read-only, no elevation: one JSON line describing the rules
#   -Action Firewall        create or replace the rules (asks for administrator permission once)
#   -Action RemoveFirewall  remove only the rules VROSTBridge created (for the uninstaller)
# Exit codes: 0 done, 1 failed, 1223 administrator permission declined, 3 Windows did not report the result.
param(
    [ValidateSet('Check','Firewall','RemoveFirewall')][string]$Action='Check',
    [string]$ClientExecutable,
    [string]$PythonExecutable
)
$ErrorActionPreference='Stop'
# The rule names and the ownership marker identify the rules VROSTBridge created, so only those are ever replaced
# or removed. VROSTBridge.Client.* never collides with the host's VROSTBridge.VR.* and VROSTBridge.Session.* rules
# on a PC that has both.
$ruleNames=@('VROSTBridge.Client.VR.TCP','VROSTBridge.Client.VR.UDP','VROSTBridge.Client.VR.mDNS','VROSTBridge.Client.Companion.TCP','VROSTBridge.Client.Companion.UDP')
$ownership='VROSTBridge client private LAN streaming and discovery'

function Format-Literal([string]$text) {
    # A single-quoted PowerShell literal. PowerShell also treats U+2018, U+2019, U+201A and U+201B as single quotes.
    foreach ($quote in @([string][char]0x27,[string][char]0x2018,[string][char]0x2019,[string][char]0x201A,[string][char]0x201B)) {
        $text=$text.Replace($quote,$quote+$quote)
    }
    return "'"+$text+"'"
}

function Test-Administrator {
    $principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-Elevated([string]$arguments) {
    # Run this script again with administrator rights (one UAC prompt) and exit with its exit code.
    # EncodedCommand avoids quoting mistakes for spaces, Unicode and apostrophes, as in host/Host-Setup.ps1.
    $command='try { & '+(Format-Literal $PSCommandPath)+' '+$arguments+'; if ($LASTEXITCODE) { exit $LASTEXITCODE }; exit 0 } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }'
    $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    # Process.Start with the runas verb is what Start-Process -Verb RunAs does, but it keeps Windows' "cancelled by
    # the user" code (1223), which Start-Process replaces with a translated message (as in host/qos.py).
    $info=New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $info.Arguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand '+$encoded
    $info.Verb='runas'
    $info.UseShellExecute=$true
    $info.WindowStyle=[System.Diagnostics.ProcessWindowStyle]::Hidden
    try {
        $elevated=[System.Diagnostics.Process]::Start($info)
    } catch {
        $failure=$_.Exception
        while ($failure) {
            if ($failure -is [System.ComponentModel.Win32Exception] -and $failure.NativeErrorCode -eq 1223) {exit 1223}
            $failure=$failure.InnerException
        }
        throw
    }
    if ($null -eq $elevated) {exit 3}
    $elevated.WaitForExit()
    exit $elevated.ExitCode
}

function Get-ExpectedRules {
    # The same ports as the Bazzite client (launcher/client_network.py PORTS), each for the program that listens:
    # the native client's ALVR control TCP 9943, stream UDP 9944 (the host profile streams over UDP only) and mDNS
    # discovery UDP 5353; the companion's diagnostics TCP 9945, network-test receiver UDP 9944 and session-discovery
    # replies UDP 9946 (the session broker on TCP 9946 is the host's, never the client's).
    return @(
        @{Name='VROSTBridge.Client.VR.TCP';Protocol='TCP';Ports=@('9943');Program=$ClientExecutable},
        @{Name='VROSTBridge.Client.VR.UDP';Protocol='UDP';Ports=@('9944');Program=$ClientExecutable},
        @{Name='VROSTBridge.Client.VR.mDNS';Protocol='UDP';Ports=@('5353');Program=$ClientExecutable},
        @{Name='VROSTBridge.Client.Companion.TCP';Protocol='TCP';Ports=@('9945');Program=$PythonExecutable},
        @{Name='VROSTBridge.Client.Companion.UDP';Protocol='UDP';Ports=@('9944','9946');Program=$PythonExecutable}
    )
}

function Get-RuleState($expected) {
    $rule=Get-NetFirewallRule -Name $expected.Name -ErrorAction SilentlyContinue
    if (-not $rule) {return 'missing'}
    if ($rule.Description -ne $ownership) {return 'foreign'}
    if ($rule.Enabled -ne 'True' -or $rule.Action -ne 'Allow' -or $rule.Direction -ne 'Inbound' -or $rule.Profile -ne 'Any') {return 'different'}
    $ports=$rule | Get-NetFirewallPortFilter
    $addresses=$rule | Get-NetFirewallAddressFilter
    $application=$rule | Get-NetFirewallApplicationFilter
    if ($ports.Protocol -ne $expected.Protocol -or (@($ports.LocalPort) -join ',') -ne (@($expected.Ports) -join ',') -or (@($addresses.RemoteAddress) -join ',') -ne 'LocalSubnet' -or $application.Program -ne $expected.Program) {return 'different'}
    return 'ready'
}

if ($Action -eq 'RemoveFirewall') {
    $owned=@()
    foreach ($name in $ruleNames) {
        $rule=Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
        if (-not $rule) {continue}
        if ($rule.Description -ne $ownership) {throw "Firewall rule $name does not carry the VROSTBridge ownership marker; it was not removed."}
        $owned+=@($rule)
    }
    if (-not $owned.Count) {Write-Output 'No VROSTBridge client firewall rules remain.';exit 0}
    if (-not (Test-Administrator)) {Invoke-Elevated '-Action RemoveFirewall'}
    $owned | Remove-NetFirewallRule
    Write-Output 'Removed only the firewall rules created by the VROSTBridge client (named VROSTBridge.Client.*).'
    exit 0
}

foreach ($path in @($ClientExecutable,$PythonExecutable)) {
    if (-not $path -or -not (Test-Path -LiteralPath $path -PathType Leaf)) {
        if ($Action -eq 'Check') {continue}
        throw "A VROSTBridge client program is missing ($path). Repair or reinstall VROSTBridge."
    }
}
if ($ClientExecutable -and (Test-Path -LiteralPath $ClientExecutable -PathType Leaf)) {$ClientExecutable=(Resolve-Path -LiteralPath $ClientExecutable).Path}
if ($PythonExecutable -and (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {$PythonExecutable=(Resolve-Path -LiteralPath $PythonExecutable).Path}

if ($Action -eq 'Check') {
    $rows=@(foreach ($expected in (Get-ExpectedRules)) {[pscustomobject]@{name=$expected.Name;state=(Get-RuleState $expected)}})
    # Inbound Block rules for either program (left by a declined Windows Firewall prompt) win over Allow rules.
    $programs=@($ClientExecutable,$PythonExecutable | Where-Object {$_})
    $blocking=@()
    try {
        $blocking=@(Get-NetFirewallApplicationFilter -ErrorAction Stop | Where-Object {$programs -contains $_.Program} | Get-NetFirewallRule -ErrorAction Stop | Where-Object {$_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block'} | ForEach-Object {[string]$_.DisplayName})
    } catch {}
    $profiles=@()
    try {$profiles=@(Get-NetConnectionProfile -ErrorAction Stop | ForEach-Object {[pscustomobject]@{alias=[string]$_.InterfaceAlias;category=[string]$_.NetworkCategory}})} catch {}
    $firewall=@()
    try {$firewall=@(Get-NetFirewallProfile -ErrorAction Stop | ForEach-Object {[pscustomobject]@{name=[string]$_.Name;enabled=[string]$_.Enabled}})} catch {}
    try {[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)} catch {}
    ConvertTo-Json -Compress -Depth 4 -InputObject ([pscustomobject]@{schema=1;rules=$rows;blocking=$blocking;profiles=$profiles;firewall=$firewall})
    exit 0
}

# -Action Firewall
$states=@(foreach ($expected in (Get-ExpectedRules)) {Get-RuleState $expected})
if ($states -contains 'foreign') {throw 'A firewall rule named VROSTBridge.Client.* already exists without the VROSTBridge ownership marker. It was not changed.'}
if (-not ($states | Where-Object {$_ -ne 'ready'})) {Write-Output 'Client home-network firewall rules are already configured.';exit 0}
if (-not (Test-Administrator)) {
    Invoke-Elevated ('-Action Firewall -ClientExecutable '+(Format-Literal $ClientExecutable)+' -PythonExecutable '+(Format-Literal $PythonExecutable))
}
foreach ($expected in (Get-ExpectedRules)) {
    $rule=Get-NetFirewallRule -Name $expected.Name -ErrorAction SilentlyContinue
    # Check again immediately before replacement, including after elevation.
    if ($rule -and $rule.Description -ne $ownership) {throw "Firewall rule $($expected.Name) is not owned by VROSTBridge; it was not changed."}
    if ($rule) {$rule | Remove-NetFirewallRule}
    New-NetFirewallRule -Name $expected.Name -DisplayName "$($expected.Name) (home network)" -Description $ownership -Program $expected.Program -Direction Inbound -Action Allow -Protocol $expected.Protocol -LocalPort $expected.Ports -Profile Any -RemoteAddress LocalSubnet | Out-Null
}
Write-Output 'Client home-network rules configured (local subnet only): VROSTBridge client streaming TCP 9943 and UDP 9944 and mDNS UDP 5353; companion diagnostics TCP 9945, network test UDP 9944 and session discovery UDP 9946. Private networks and the local subnet only.'
exit 0
