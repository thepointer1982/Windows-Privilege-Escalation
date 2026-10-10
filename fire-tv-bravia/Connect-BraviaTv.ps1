<#
.SYNOPSIS
    Netzwerk- und Diagnose-Tool (PowerShell), um über einen Fire-TV-Stick am
    Sony-Bravia-Fernseher die Verbindung im Heimnetz herzustellen.

.DESCRIPTION
    PowerShell-Pendant zu firetv_bravia_connect.py, für Windows-Nutzer.
     WICHTIG: Auf einem Rechner IM SELBEN NETZ wie der TV ausführen (Heim-PC/
    Laptop). Aus einer Cloud/CI-Umgebung ist der TV nicht erreichbar - das ist
    Netzwerk-Topologie, keine Frage des Werkzeugs.

    Fire TV und moderne Bravia laufen auf Android TV -> Steuerung per ADB over
    Network (TCP 5555). Die Bravia bietet zusätzlich Simple IP Control (20060)
    und die Scalar Web API (80).

.PARAMETER Command
    scan | connect | diagnose  (Standard: diagnose)

.PARAMETER Ip
    Ziel-IP (für 'connect', optional für die anderen).

.PARAMETER Subnet
    CIDR-Subnetz, z.B. 192.168.178.0/24. Standard: automatisch ermitteln.

.EXAMPLE
    .\Connect-BraviaTv.ps1 diagnose
.EXAMPLE
    .\Connect-BraviaTv.ps1 scan -Subnet 192.168.178.0/24
.EXAMPLE
    .\Connect-BraviaTv.ps1 connect -Ip 192.168.178.42
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('scan', 'connect', 'diagnose')]
    [string]$Command = 'diagnose',

    [string]$Ip,
    [string]$Subnet,
    [int]$Port = 5555,
    [int]$TimeoutMs = 600,
    [int]$Workers = 100,
    [switch]$Json,
    [switch]$NoConnect
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# --------------------------------------------------------------------------- #
# Bekannte Ports
# --------------------------------------------------------------------------- #
$KnownPorts = @{
    5555  = 'adb'; 5556 = 'adb-tls'; 8009 = 'googlecast'
    8008  = 'firetv-companion'; 7000 = 'firetv-companion'
    20060 = 'sony-simple-ip'; 80 = 'http-scalar'; 443 = 'https'
}
$ProbePorts = @(5555, 20060, 8009, 8008, 7000, 80, 5556, 443)
$AdbPort = 5555

# --------------------------------------------------------------------------- #
# Ausgabe-Helfer (Fortschritt nach stderr, Daten nach stdout)
# --------------------------------------------------------------------------- #
function Write-Info { param($m) [Console]::Error.WriteLine("[*] $m") }
function Write-Ok   { param($m) [Console]::Error.WriteLine("[+] $m") }
function Write-Warn { param($m) [Console]::Error.WriteLine("[!] $m") }
function Write-Err  { param($m) [Console]::Error.WriteLine("[-] $m") }

# --------------------------------------------------------------------------- #
# Netz-Ermittlung
# --------------------------------------------------------------------------- #
function Get-LocalSubnet {
    # Aktives IPv4-Interface mit Default-Gateway bevorzugen.
    try {
        $cfg = Get-NetIPConfiguration -ErrorAction Stop |
            Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address } |
            Select-Object -First 1
        if ($cfg) {
            $addr = $cfg.IPv4Address.IPAddress
            $octets = $addr.Split('.')
            return "$($octets[0]).$($octets[1]).$($octets[2]).0/24"
        }
    } catch {
        # Fallback für ältere Systeme
    }
    $ip = (Test-Connection -ComputerName $env:COMPUTERNAME -Count 1 -ErrorAction SilentlyContinue).IPV4Address.IPAddressToString
    if ($ip) {
        $o = $ip.Split('.'); return "$($o[0]).$($o[1]).$($o[2]).0/24"
    }
    return $null
}

function Expand-Subnet {
    param([string]$Cidr)
    $parts = $Cidr.Split('/')
    $base = $parts[0].Split('.')
    if ($parts.Count -lt 2 -or [int]$parts[1] -ne 24) {
        throw "Nur /24-Subnetze werden unterstützt (z.B. 192.168.178.0/24)."
    }
    1..254 | ForEach-Object { "$($base[0]).$($base[1]).$($base[2]).$_" }
}

# --------------------------------------------------------------------------- #
# Paralleler Port-/Ping-Scan über RunspacePool (PS 5.1-kompatibel)
# --------------------------------------------------------------------------- #
function Invoke-HostScan {
    param([string[]]$Ips, [int[]]$Ports, [int]$Timeout, [int]$MaxWorkers)

    $probe = {
        param($ip, $ports, $timeoutMs)
        $open = New-Object System.Collections.ArrayList
        foreach ($p in $ports) {
            $client = New-Object System.Net.Sockets.TcpClient
            try {
                $iar = $client.BeginConnect($ip, $p, $null, $null)
                if ($iar.AsyncWaitHandle.WaitOne($timeoutMs, $false)) {
                    try { $client.EndConnect($iar); [void]$open.Add($p) } catch {}
                }
            } catch {} finally { $client.Close() }
        }
        $alive = $open.Count -gt 0
        if (-not $alive) {
            try {
                $ping = New-Object System.Net.NetworkInformation.Ping
                $reply = $ping.Send($ip, $timeoutMs)
                if ($reply.Status -eq 'Success') { $alive = $true }
            } catch {}
        }
        [pscustomobject]@{ Ip = $ip; OpenPorts = @($open); Alive = $alive }
    }

    $pool = [runspacefactory]::CreateRunspacePool(1, $MaxWorkers)
    $pool.Open()
    $jobs = New-Object System.Collections.ArrayList
    foreach ($ip in $Ips) {
        $ps = [powershell]::Create()
        $ps.RunspacePool = $pool
        [void]$ps.AddScript($probe).AddArgument($ip).AddArgument($Ports).AddArgument($Timeout)
        [void]$jobs.Add([pscustomobject]@{ PS = $ps; Handle = $ps.BeginInvoke() })
    }
    $results = New-Object System.Collections.ArrayList
    foreach ($j in $jobs) {
        $r = $j.PS.EndInvoke($j.Handle)
        if ($r) { [void]$results.Add($r[0]) }
        $j.PS.Dispose()
    }
    $pool.Close(); $pool.Dispose()
    $results | Where-Object { $_.Alive }
}

function Resolve-HostNameSafe {
    param([string]$Ip)
    try { return [System.Net.Dns]::GetHostEntry($Ip).HostName } catch { return $null }
}

# --------------------------------------------------------------------------- #
# Geräte-Klassifizierung
# --------------------------------------------------------------------------- #
function Get-DeviceClass {
    param($DeviceHost)
    $ports = @($DeviceHost.OpenPorts)
    $name = if ($DeviceHost.Hostname) { $DeviceHost.Hostname.ToLower() } else { '' }
    $hasAdb = ($ports -contains 5555) -or ($ports -contains 5556)
    $notes = New-Object System.Collections.ArrayList

    if ($ports -contains 20060) {
        [void]$notes.Add('Sony Simple IP Control (20060) offen')
        if ($hasAdb) { [void]$notes.Add('ADB (5555) offen - Android-TV-Bravia, per adb steuerbar') }
        return @{ Type = 'bravia'; Confidence = 'high'; Notes = $notes }
    }
    if ($name -match 'bravia|sony') {
        [void]$notes.Add("Hostname deutet auf Bravia hin: $($DeviceHost.Hostname)")
        return @{ Type = 'bravia'; Confidence = 'medium'; Notes = $notes }
    }
    if ($name -match 'amazon|firetv|fire-tv|aftv|aft') {
        [void]$notes.Add("Hostname deutet auf Fire TV hin: $($DeviceHost.Hostname)")
        if ($hasAdb) { [void]$notes.Add('ADB (5555) offen - per adb verbindbar') }
        return @{ Type = 'firetv'; Confidence = 'medium'; Notes = $notes }
    }
    if (($ports -contains 7000) -or ($ports -contains 8008)) {
        if ($hasAdb) {
            [void]$notes.Add('Fire-TV-Companion-Port + ADB offen')
            return @{ Type = 'firetv'; Confidence = 'medium'; Notes = $notes }
        }
    }
    if ($hasAdb) {
        [void]$notes.Add('ADB (5555) offen - Android-TV-Geraet (Fire TV oder Bravia)')
        return @{ Type = 'androidtv'; Confidence = 'medium'; Notes = $notes }
    }
    if ($ports -contains 8009) {
        [void]$notes.Add('Google Cast (8009) offen')
        return @{ Type = 'androidtv'; Confidence = 'low'; Notes = $notes }
    }
    if ($ports -contains 80) {
        [void]$notes.Add('HTTP/Scalar (80) offen - koennte Bravia Web API sein')
        return @{ Type = 'generic'; Confidence = 'low'; Notes = $notes }
    }
    return @{ Type = 'unknown'; Confidence = 'low'; Notes = $notes }
}

# --------------------------------------------------------------------------- #
# ADB
# --------------------------------------------------------------------------- #
function Test-AdbAvailable { return [bool](Get-Command adb -ErrorAction SilentlyContinue) }

function Invoke-Adb {
    param([string[]]$AdbArgs, [int]$TimeoutSec = 20)
    $out = & adb @AdbArgs 2>&1 | Out-String
    return $out.Trim()
}

function Connect-BraviaAdb {
    param([string]$TargetIp, [int]$TargetPort = 5555)
    if (-not (Test-AdbAvailable)) {
        return @{ Success = $false; Message = 'adb nicht gefunden. Android Platform Tools installieren (developer.android.com/tools/releases/platform-tools) und PATH setzen.' }
    }
    [void](Invoke-Adb -AdbArgs @('start-server'))
    $target = "${TargetIp}:${TargetPort}"
    $out = Invoke-Adb -AdbArgs @('connect', $target)
    $low = $out.ToLower()
    if ($low -match 'unauthorized') {
        return @{ Success = $false; Message = "$out`n    -> Am Fernseher das ADB-Debugging-Popup 'Zulassen' bestätigen." }
    }
    $ok = ($low -match 'connected to') -or ($low -match 'already connected')
    return @{ Success = $ok; Message = $out }
}

# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
function Show-Host {
    param($H)
    $label = switch ($H.Type) {
        'firetv' { 'Fire TV' }; 'bravia' { 'Sony Bravia' }
        'androidtv' { 'Android TV' }; 'generic' { 'generisch' }; default { 'unbekannt' }
    }
    $name = if ($H.Hostname) { " ($($H.Hostname))" } else { '' }
    $portStr = (@($H.OpenPorts) | ForEach-Object {
            $svc = if ($KnownPorts.ContainsKey($_)) { $KnownPorts[$_] } else { '?' }
            "$_/$svc"
        }) -join ', '
    if (-not $portStr) { $portStr = '-' }
    Write-Output "  $($H.Ip)$name"
    Write-Output "      Typ       : $label  [Konfidenz: $($H.Confidence)]"
    Write-Output "      Ports     : $portStr"
    foreach ($n in @($H.Notes)) { Write-Output "      Hinweis   : $n" }
}

function Get-EnrichedHosts {
    param([string]$Cidr, [int]$Timeout, [int]$MaxWorkers)
    $ips = Expand-Subnet -Cidr $Cidr
    Write-Info "Scanne $($ips.Count) Adressen in $Cidr (Ports: $($ProbePorts -join ', ')) ..."
    $raw = Invoke-HostScan -Ips $ips -Ports $ProbePorts -Timeout $Timeout -MaxWorkers $MaxWorkers
    $hosts = foreach ($r in $raw) {
        $hostname = Resolve-HostNameSafe -Ip $r.Ip
        $cls = Get-DeviceClass -DeviceHost ([pscustomobject]@{ OpenPorts = $r.OpenPorts; Hostname = $hostname })
        [pscustomobject]@{
            Ip = $r.Ip; Hostname = $hostname; OpenPorts = @($r.OpenPorts)
            Type = $cls.Type; Confidence = $cls.Confidence; Notes = @($cls.Notes)
        }
    }
    @($hosts | Sort-Object { [version]($_.Ip) })
}

# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
function Resolve-TargetSubnet {
    param([string]$Explicit)
    if ($Explicit) { return $Explicit }
    $s = Get-LocalSubnet
    if (-not $s) { throw "Konnte kein Subnetz ermitteln. Bitte -Subnet 192.168.x.0/24 angeben." }
    return $s
}

function Invoke-ScanCommand {
    $net = Resolve-TargetSubnet -Explicit $Subnet
    $hosts = Get-EnrichedHosts -Cidr $net -Timeout $TimeoutMs -MaxWorkers $Workers
    if ($Json) { $hosts | ConvertTo-Json -Depth 5; return 0 }
    if (-not $hosts) { Write-Warn 'Keine erreichbaren Hosts gefunden.'; return 1 }
    Write-Ok "$($hosts.Count) erreichbare Hosts:"
    foreach ($h in $hosts) { Show-Host -H $h }
    $cands = @($hosts | Where-Object { $_.Type -in 'firetv', 'bravia', 'androidtv' })
    if ($cands) {
        Write-Ok ("TV-Kandidat(en): " + (($cands | ForEach-Object { "$($_.Ip) ($($_.Type))" }) -join ', '))
    } else {
        Write-Warn "Keine eindeutigen Fire-TV-/Bravia-Kandidaten. Ggf. am TV ADB-Debugging aktivieren."
    }
    return 0
}

function Invoke-ConnectCommand {
    if (-not $Ip) { Write-Err "Für 'connect' bitte -Ip angeben."; return 2 }
    Write-Ok "Verbinde per adb -> ${Ip}:${Port}"
    $res = Connect-BraviaAdb -TargetIp $Ip -TargetPort $Port
    Write-Output "    $($res.Message)"
    if (-not $res.Success) {
        Write-Err 'ADB-Verbindung nicht bestätigt.'
        if (Test-AdbAvailable) { Write-Output (Invoke-Adb -AdbArgs @('devices', '-l')) }
        return 1
    }
    Write-Ok 'ADB-Verbindung steht.'
    if (Test-AdbAvailable) {
        $model = Invoke-Adb -AdbArgs @('-s', "${Ip}:${Port}", 'shell', 'getprop', 'ro.product.model')
        if ($model) { Write-Ok "Geraetemodell: $model" }
        Write-Output (Invoke-Adb -AdbArgs @('devices', '-l'))
    }
    return 0
}

function Invoke-DiagnoseCommand {
    $net = Resolve-TargetSubnet -Explicit $Subnet
    Write-Info "Subnetz: $net   adb verfügbar: $(if (Test-AdbAvailable) {'ja'} else {'nein'})"
    $hosts = Get-EnrichedHosts -Cidr $net -Timeout $TimeoutMs -MaxWorkers $Workers
    $cands = @($hosts | Where-Object { $_.Type -in 'firetv', 'bravia', 'androidtv' } |
        Sort-Object @{ Expression = { if ($_.OpenPorts -contains $AdbPort) { 0 } else { 1 } } },
        @{ Expression = { @{high = 0; medium = 1; low = 2 }[$_.Confidence] } })

    $connection = $null
    if ($cands -and -not $NoConnect) {
        $best = $cands[0]
        if ($best.OpenPorts -contains $AdbPort) {
            Write-Info "Bester Kandidat: $($best.Ip) ($($best.Type)) - versuche ADB-Verbindung."
            $r = Connect-BraviaAdb -TargetIp $best.Ip -TargetPort $AdbPort
            $connection = @{ Ip = $best.Ip; Success = $r.Success; Message = $r.Message }
        } else {
            Write-Warn "Bester Kandidat $($best.Ip) hat keinen offenen ADB-Port (5555). Am Gerät ADB-Debugging aktivieren."
        }
    }

    if ($Json) {
        [pscustomobject]@{ Subnet = $net; Hosts = $hosts; Connection = $connection } |
            ConvertTo-Json -Depth 6
        return 0
    }

    if ($hosts) {
        Write-Ok "$($hosts.Count) erreichbare Hosts:"
        foreach ($h in $hosts) { Show-Host -H $h }
    } else { Write-Warn 'Keine erreichbaren Hosts gefunden.' }

    if ($cands) {
        Write-Ok 'TV-Kandidaten (priorisiert):'
        foreach ($h in $cands) { Write-Output ("  - {0,-15} {1,-10} [{2}]" -f $h.Ip, $h.Type, $h.Confidence) }
    } else { Write-Warn 'Keine Fire-TV-/Bravia-Kandidaten erkannt.' }

    if ($connection -and $connection.Success) {
        Write-Ok "Verbindung hergestellt zu $($connection.Ip)"
        if (Test-AdbAvailable) { Write-Output (Invoke-Adb -AdbArgs @('devices', '-l')) }
    } elseif ($connection) {
        Write-Warn "Verbindungsversuch zu $($connection.Ip) nicht bestätigt:"
        Write-Output "    $($connection.Message)"
    }
    return 0
}

# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
switch ($Command) {
    'scan'     { exit (Invoke-ScanCommand) }
    'connect'  { exit (Invoke-ConnectCommand) }
    'diagnose' { exit (Invoke-DiagnoseCommand) }
}
