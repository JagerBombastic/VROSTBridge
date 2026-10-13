# VROSTBridge client: power commands for SteamVR 2.0 base stations (named LHB-XXXXXXXX) over Bluetooth Low Energy,
# with Windows' own Bluetooth API (WinRT) from Windows PowerShell 5.1. launcher/base_stations.py runs it, and
# closing VROSTBridge Client starts it on its own to turn the base stations off.
#   -Action on|off|standby  the command (one byte to the power characteristic: 0x01 on, 0x00 sleep, 0x02 standby)
#   -Known ADDRESS=NAME,... stations found before (AA:BB:CC:DD:EE:FF=LHB-XXXXXXXX): reached directly, without a search
#   -ScanSeconds N          how long to look for stations when none are known
#   -StatePath PATH         base-stations.json to update with what was found and done (unless -NoState)
# Output: one JSON line {"stations":[{"name","address","result":"sent|failed","state","error"}],"error":""}.
# Nothing is paired, trusted or changed in Windows' Bluetooth settings; devices not named LHB- are never contacted.
param(
    [ValidateSet('on','off','standby')][string]$Action='off',
    [string]$Known='',
    [ValidateRange(1,30)][int]$ScanSeconds=6,
    [string]$StatePath='',
    [switch]$NoState
)
$ErrorActionPreference='Stop'
$serviceUuid=[Guid]'00001523-1212-efde-1523-785feabcd124'
$powerUuid=[Guid]'00001525-1212-efde-1523-785feabcd124'
$commands=@{ 'off'=0; 'on'=1; 'standby'=2 }
$stateNames=@{ 0='asleep'; 2='standby'; 11='on'; 1='starting'; 8='starting'; 9='starting' }
$namePattern='^LHB-[0-9A-Fa-f]{8}$'
$addressPattern='^[0-9A-F]{2}(:[0-9A-F]{2}){5}$'

function Write-Outcome($stations, [string]$problem) {
    $value=[ordered]@{ stations=@($stations); error=$problem }
    if (-not $NoState -and $StatePath) { Save-State $stations $problem }
    Write-Output ($value | ConvertTo-Json -Compress -Depth 5)
}

function Save-State($stations, [string]$problem) {
    # As launcher/base_stations.py records it: the stations found (merged with earlier ones) and this command.
    try {
        $known=[ordered]@{}
        if (Test-Path -LiteralPath $StatePath) {
            $saved=Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($row in @($saved.stations)) {
                if ($row.address -match $addressPattern -and $row.name -match $namePattern) { $known[$row.address]=$row.name }
            }
        }
        foreach ($row in @($stations)) { if ($row.address -match $addressPattern) { $known[$row.address]=$row.name } }
        $done=@($stations | Where-Object { $_.result -eq 'sent' }).Count
        $message=if ($problem) { $problem } else { "$done of $(@($stations).Count) base stations turned $Action." }
        $rows=@(foreach ($key in $known.Keys) { [ordered]@{ address=$key; name=$known[$key] } })
        $results=@(foreach ($row in @($stations)) { [ordered]@{ name=$row.name; result=$row.result; state=$row.state } })
        $value=[ordered]@{ version=1; stations=$rows; last=[ordered]@{ action=$Action;
            time=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds(); results=$results; message=$message } }
        $folder=Split-Path -Parent $StatePath
        if ($folder -and -not (Test-Path -LiteralPath $folder)) { New-Item -ItemType Directory -Path $folder -Force | Out-Null }
        [IO.File]::WriteAllText($StatePath, ($value | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))
    } catch { }
}

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null=[Windows.Devices.Bluetooth.BluetoothAdapter, Windows.Devices.Bluetooth, ContentType=WindowsRuntime]
    $null=[Windows.Devices.Bluetooth.BluetoothLEDevice, Windows.Devices.Bluetooth, ContentType=WindowsRuntime]
    $null=[Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher, Windows.Devices.Bluetooth.Advertisement, ContentType=WindowsRuntime]
    $null=[Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService, Windows.Devices.Bluetooth.GenericAttributeProfile, ContentType=WindowsRuntime]
    $null=[Windows.Devices.Radios.Radio, Windows.Devices.Radios, ContentType=WindowsRuntime]
    $null=[Windows.Storage.Streams.DataWriter, Windows.Storage.Streams, ContentType=WindowsRuntime]
    $null=[Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType=WindowsRuntime]
} catch {
    Write-Outcome @() "Windows' Bluetooth API is not available on this PC."
    exit 1
}

# WinRT's IAsyncOperation<T> as a .NET task, waited for with a time limit.
$asTask=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function Wait-Operation($operation, [Type]$type, [int]$milliseconds=10000) {
    $task=$asTask.MakeGenericMethod($type).Invoke($null, @($operation))
    if (-not $task.Wait($milliseconds)) { throw 'Windows did not answer in time' }
    return $task.Result
}

try {
    $adapter=Wait-Operation ([Windows.Devices.Bluetooth.BluetoothAdapter]::GetDefaultAsync()) ([Windows.Devices.Bluetooth.BluetoothAdapter])
} catch { $adapter=$null }
if (-not $adapter -or -not $adapter.IsLowEnergySupported) {
    Write-Outcome @() 'This PC has no Bluetooth Low Energy adapter, so the base stations were not changed.'
    exit 1
}
try {
    $radio=Wait-Operation ($adapter.GetRadioAsync()) ([Windows.Devices.Radios.Radio])
    if ($radio -and $radio.State -ne [Windows.Devices.Radios.RadioState]::On) {
        Write-Outcome @() 'Bluetooth is off on this PC, so the base stations were not changed.'
        exit 1
    }
} catch { }

# The stations to reach: those known from before, else a short search for LHB- names.
$targets=[ordered]@{}
foreach ($entry in ($Known -split ',')) {
    $parts=$entry.Trim() -split '=', 2
    $address=$parts[0].Trim().ToUpperInvariant()
    $name=if ($parts.Count -gt 1) { $parts[1].Trim() } else { '' }
    if ($address -match $addressPattern -and $name -match $namePattern) { $targets[$address]='LHB-'+$name.Substring(4).ToUpperInvariant() }
}
if ($targets.Count -eq 0) {
    $watcher=New-Object Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher
    $watcher.ScanningMode=[Windows.Devices.Bluetooth.Advertisement.BluetoothLEScanningMode]::Active
    $source='VROSTBridge.BaseStations.'+[Guid]::NewGuid().ToString('N')
    Register-ObjectEvent -InputObject $watcher -EventName Received -SourceIdentifier $source | Out-Null
    try {
        $watcher.Start()
        $deadline=[DateTime]::UtcNow.AddSeconds($ScanSeconds)
        while ([DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 200
            foreach ($seen in @(Get-Event -SourceIdentifier $source -ErrorAction SilentlyContinue)) {
                $name=$seen.SourceEventArgs.Advertisement.LocalName
                if ($name -match $namePattern) {
                    $hex='{0:X12}' -f [uint64]$seen.SourceEventArgs.BluetoothAddress
                    $address=($hex -split '(..)' | Where-Object { $_ }) -join ':'
                    $targets[$address]='LHB-'+$name.Substring(4).ToUpperInvariant()
                }
                Remove-Event -EventIdentifier $seen.EventIdentifier
            }
        }
    } finally {
        try { $watcher.Stop() } catch { }
        Unregister-Event -SourceIdentifier $source -ErrorAction SilentlyContinue
    }
}
if ($targets.Count -eq 0) {
    Write-Outcome @() 'No SteamVR 2.0 base stations were found nearby.'
    exit 1
}

$cache=[Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached
$results=@()
foreach ($address in @($targets.Keys)) {
    $row=[ordered]@{ name=$targets[$address]; address=$address; result='failed'; state=$null; error='' }
    # 1.0.0.9: a station that did not answer is tried again after a moment; 1.0.0.10: three tries, 2 s apart.
    for ($attempt=1; $attempt -le 3 -and $row.result -ne 'sent'; $attempt++) {
    if ($attempt -gt 1) { Start-Sleep -Milliseconds 2000; $row.error='' }
    $device=$null; $service=$null
    try {
        $number=[Convert]::ToUInt64(($address -replace ':',''), 16)
        $device=Wait-Operation ([Windows.Devices.Bluetooth.BluetoothLEDevice]::FromBluetoothAddressAsync($number)) ([Windows.Devices.Bluetooth.BluetoothLEDevice]) 15000
        if (-not $device) { throw 'not reached' }
        $services=Wait-Operation ($device.GetGattServicesForUuidAsync($serviceUuid, $cache)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceServicesResult]) 15000
        if ($services.Status -ne [Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus]::Success -or $services.Services.Count -lt 1) {
            throw "its power service did not answer ($($services.Status))"
        }
        $service=$services.Services[0]
        $found=Wait-Operation ($service.GetCharacteristicsForUuidAsync($powerUuid, $cache)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristicsResult]) 10000
        if ($found.Status -ne [Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus]::Success -or $found.Characteristics.Count -lt 1) {
            throw "its power setting did not answer ($($found.Status))"
        }
        $characteristic=$found.Characteristics[0]
        $writer=New-Object Windows.Storage.Streams.DataWriter
        $writer.WriteByte([byte]$commands[$Action])
        $status=Wait-Operation ($characteristic.WriteValueAsync($writer.DetachBuffer(), [Windows.Devices.Bluetooth.GenericAttributeProfile.GattWriteOption]::WriteWithoutResponse)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus]) 10000
        if ($status -ne [Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus]::Success) { throw "the command was refused ($status)" }
        $row.result='sent'
        try {
            $read=Wait-Operation ($characteristic.ReadValueAsync($cache)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattReadResult]) 8000
            if ($read.Status -eq [Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus]::Success) {
                $value=[int][Windows.Storage.Streams.DataReader]::FromBuffer($read.Value).ReadByte()
                if ($stateNames.ContainsKey($value)) { $row.state=$stateNames[$value] }
            }
        } catch { }
    } catch {
        $row.error=([string]$_.Exception.Message).Trim()
        if ($row.error.Length -gt 160) { $row.error=$row.error.Substring(0,160) }
    } finally {
        if ($service) { try { $service.Dispose() } catch { } }
        if ($device) { try { $device.Dispose() } catch { } }
    }
    }
    if (-not $row.name) { $row.name=$address }
    $results+=[pscustomobject]$row
}
Write-Outcome $results ''
if (@($results | Where-Object { $_.result -ne 'sent' }).Count) { exit 1 }
exit 0
