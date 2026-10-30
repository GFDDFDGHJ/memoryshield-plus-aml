#requires -Version 5.1
<#
.SYNOPSIS
Configure only the hlq-1 relay for the isolated textual v3.16.0 backend.
.DESCRIPTION
Run this saved script in Administrator PowerShell ON hlq-1, after Tailscale
is online. No API key is needed or read. The backend must already be ready.

  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\configure-hlq1-ingress.ps1
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\configure-hlq1-ingress.ps1 -MigrateLegacy8080
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\configure-hlq1-ingress.ps1 -Rollback

The only network changes are a loopback TCP portproxy on 127.0.0.1:8380
and this node's Funnel HTTPS port 443. Existing conflicting configuration
is refused. -MigrateLegacy8080 permits replacing only the exact existing
hlq-1 HTTPS root proxy to http://127.0.0.1:8080 with this relay. -Rollback
restores that original root proxy when the current route is still the exact
route owned by this script. Other ports, devices, keys, firewall rules and
services are not reset. If IP Helper is stopped, it is started and reported;
rollback leaves that shared Windows service running.
Backups and the ownership journal are saved beside this script. Keep them
with this script until the relay is no longer needed.
#>
[CmdletBinding()]
param([switch]$Rollback, [switch]$MigrateLegacy8080)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ExpectedDns = 'hlq-1.tail50f4d4.ts.net'
$FunnelKey = $ExpectedDns + ':443'
$ListenAddress = '127.0.0.1'
$RelayPort = 8380
$TargetAddress = '100.93.38.9'
$TargetUrl = 'http://100.93.38.9:8380'
$LocalUrl = 'http://127.0.0.1:8380'
$LegacyLocalUrl = 'http://127.0.0.1:8080'
$ExpectedVersion = 'v3.16.0'
$ExpectedModel = 'gpt-4o-mini'
$MappingName = '127.0.0.1/8380'
$MappingValue = '100.93.38.9/8380'
$RegistryBase = 'HKLM:\SYSTEM\CurrentControlSet\Services\PortProxy'
$StatePath = Join-Path $PSScriptRoot '.hlq1-ingress-state.json'
$BackupRoot = Join-Path $PSScriptRoot 'hlq1-ingress-backups'
$LockPath = Join-Path $PSScriptRoot '.hlq1-ingress.lock'
$script:Journal = $null
$script:JournalPath = $StatePath

function Get-PropertyValue {
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Invoke-Native {
    param([string]$File, [string[]]$Arguments, [string]$Description)
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $nativeOutput = @(& $File @Arguments 2>&1)
        $nativeCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    if ($nativeCode -ne 0) {
        throw ($Description + ' failed (exit ' + $nativeCode + '). Native output is not printed.')
    }
    return (($nativeOutput | ForEach-Object { $_.ToString() }) -join "`n")
}

function Save-Json {
    param([string]$Path, $Value)
    $encoded = ConvertTo-Json -InputObject $Value -Depth 30
    $temporaryPath = $Path + '.new'
    [System.IO.File]::WriteAllText($temporaryPath, $encoded, (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporaryPath -Destination $Path -Force
}

function Save-Journal {
    if ($null -ne $script:Journal) {
        Save-Json -Path $script:JournalPath -Value $script:Journal
    }
}

function Get-FunnelConfiguration {
    $json = Invoke-Native -File $script:TailscaleExe -Arguments @('funnel', 'status', '--json') -Description 'Read Funnel configuration'
    if ([string]::IsNullOrWhiteSpace($json)) { return [pscustomobject]@{} }
    try { return ($json | ConvertFrom-Json) }
    catch { throw 'Funnel status was not valid JSON; nothing was changed.' }
}

function Get-Funnel443Scope {
    param($Configuration)
    $tcp = Get-PropertyValue $Configuration 'TCP'
    $web = Get-PropertyValue $Configuration 'Web'
    $allow = Get-PropertyValue $Configuration 'AllowFunnel'
    $tcp443 = Get-PropertyValue $tcp '443'
    $web443 = [ordered]@{}
    $allow443 = [ordered]@{}
    if ($null -ne $web) {
        foreach ($entry in $web.PSObject.Properties) {
            if ($entry.Name -match ':443$') { $web443[$entry.Name] = $entry.Value }
        }
    }
    if ($null -ne $allow) {
        foreach ($entry in $allow.PSObject.Properties) {
            if ($entry.Name -match ':443$') { $allow443[$entry.Name] = $entry.Value }
        }
    }
    $foreground443 = $false
    $foreground = Get-PropertyValue $Configuration 'Foreground'
    if ($null -ne $foreground) {
        foreach ($entry in $foreground.PSObject.Properties) {
            $scope = Get-Funnel443Scope $entry.Value
            if ($scope.Present) { $foreground443 = $true }
        }
    }
    return [pscustomobject]@{
        Present = (($null -ne $tcp443) -or $web443.Count -gt 0 -or $allow443.Count -gt 0 -or $foreground443)
        TCP443 = $tcp443
        Web443 = [pscustomobject]$web443
        AllowFunnel443 = [pscustomobject]$allow443
        Foreground443Present = $foreground443
    }
}

function Test-ExactFunnel443 {
    param($Scope, [string]$RootProxy)
    if (-not $Scope.Present -or $Scope.Foreground443Present) { return $false }
    if ($null -eq $Scope.TCP443) { return $false }
    if (@($Scope.TCP443.PSObject.Properties).Count -ne 1) { return $false }
    if ((Get-PropertyValue $Scope.TCP443 'HTTPS') -isnot [bool] -or
        (Get-PropertyValue $Scope.TCP443 'HTTPS') -cne $true) { return $false }
    if (@($Scope.Web443.PSObject.Properties).Count -ne 1) { return $false }
    if (@($Scope.AllowFunnel443.PSObject.Properties).Count -ne 1) { return $false }
    if ((Get-PropertyValue $Scope.AllowFunnel443 $FunnelKey) -isnot [bool] -or
        (Get-PropertyValue $Scope.AllowFunnel443 $FunnelKey) -cne $true) { return $false }
    $hostWeb = Get-PropertyValue $Scope.Web443 $FunnelKey
    if ($null -eq $hostWeb -or @($hostWeb.PSObject.Properties).Count -ne 1) { return $false }
    $handlers = Get-PropertyValue $hostWeb 'Handlers'
    if ($null -eq $handlers -or @($handlers.PSObject.Properties).Count -ne 1) { return $false }
    $rootHandler = Get-PropertyValue $handlers '/'
    if ($null -eq $rootHandler -or @($rootHandler.PSObject.Properties).Count -ne 1) { return $false }
    return ((Get-PropertyValue $rootHandler 'Proxy') -ceq $RootProxy)
}

function Test-DesiredFunnel443 {
    param($Scope)
    return (Test-ExactFunnel443 -Scope $Scope -RootProxy $LocalUrl)
}

function Test-LegacyFunnel443 {
    param($Scope)
    return (Test-ExactFunnel443 -Scope $Scope -RootProxy $LegacyLocalUrl)
}

function Get-Portproxy8380 {
    $rules = @()
    foreach ($kind in @('v4tov4', 'v4tov6', 'v6tov4', 'v6tov6')) {
        $registryPath = Join-Path $RegistryBase ($kind + '\tcp')
        if (-not (Test-Path -LiteralPath $registryPath)) { continue }
        $registryKey = Get-Item -LiteralPath $registryPath
        foreach ($name in $registryKey.GetValueNames()) {
            if ($name -match '/8380$') {
                $rules += [pscustomobject]@{
                    Kind = $kind
                    Listen = $name
                    Connect = [string]$registryKey.GetValue($name)
                }
            }
        }
    }
    return $rules
}

function Test-DesiredPortproxy {
    param([object[]]$Rules)
    return ($Rules.Count -eq 1 -and $Rules[0].Kind -ceq 'v4tov4' -and
        $Rules[0].Listen -ceq $MappingName -and $Rules[0].Connect -ceq $MappingValue)
}

function Assert-ReadyHealth {
    param([string]$Url)
    Add-Type -AssemblyName System.Net.Http
    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.AllowAutoRedirect = $false
    $handler.UseProxy = $false
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(15)
    $response = $null
    try {
        $response = $client.GetAsync($Url + '/health').GetAwaiter().GetResult()
        if ([int]$response.StatusCode -ne 200) { throw 'Health did not return HTTP 200.' }
        $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        $health = $body | ConvertFrom-Json
        if ((Get-PropertyValue $health 'status') -cne 'ok' -or
            (Get-PropertyValue $health 'version') -cne $ExpectedVersion -or
            (Get-PropertyValue $health 'add_model') -cne $ExpectedModel -or
            (Get-PropertyValue $health 'add_model_required') -cne $true -or
            (Get-PropertyValue $health 'add_model_configured') -cne $true) {
            throw 'Health does not identify the ready v3.16.0 GPT Add backend.'
        }
    }
    catch { throw ('Health verification failed for ' + $Url + '. No response body is printed.') }
    finally {
        if ($null -ne $response) { $response.Dispose() }
        $client.Dispose()
        $handler.Dispose()
    }
}

function Assert-JournalScope {
    param($Journal)
    if ((Get-PropertyValue $Journal 'FormatVersion') -ne 1 -or
        (Get-PropertyValue $Journal 'ExpectedDns') -cne $ExpectedDns -or
        (Get-PropertyValue $Journal 'TargetAddress') -cne $TargetAddress -or
        (Get-PropertyValue $Journal 'RelayPort') -ne $RelayPort -or
        (Get-PropertyValue $Journal 'LocalUrl') -cne $LocalUrl) {
        throw 'Ownership journal has a different scope. Review it manually; nothing was changed.'
    }
    foreach ($flag in @('Active', 'OwnsPortproxy', 'OwnsFunnel443', 'IpHelperStarted')) {
        if ((Get-PropertyValue $Journal $flag) -isnot [bool]) {
            throw 'Ownership journal is incomplete. Review it manually; nothing was changed.'
        }
    }
    # Format 1 journals written before migration support have no such flag.
    # Only an explicit JSON boolean can authorize restoring the known old route.
    $restoreProperty = $Journal.PSObject.Properties['RestoreLegacy8080']
    if ($null -eq $restoreProperty) {
        $Journal | Add-Member -MemberType NoteProperty -Name RestoreLegacy8080 -Value $false
    }
    elseif ($restoreProperty.Value -isnot [bool]) {
        throw 'Ownership journal has an invalid migration flag. Review it manually; nothing was changed.'
    }
}

function Undo-OwnedRoutes {
    # Only restore/remove a route when both its journal ownership and current
    # exact configuration match. Never overwrite an administrator's later edit.
    $issues = @()
    if ($script:Journal.OwnsFunnel443) {
        try {
            $scope = Get-Funnel443Scope (Get-FunnelConfiguration)
            if ($script:Journal.RestoreLegacy8080) {
                if (Test-DesiredFunnel443 $scope) {
                    $null = Invoke-Native -File $script:TailscaleExe -Arguments @('funnel', '--bg', '--https=443', '--yes', $LegacyLocalUrl) -Description 'Restore original hlq-1 root proxy'
                    if (-not (Test-LegacyFunnel443 (Get-Funnel443Scope (Get-FunnelConfiguration)))) {
                        throw 'The original Funnel 443 route was not restored exactly.'
                    }
                }
                elseif (-not (Test-LegacyFunnel443 $scope)) {
                    throw 'Funnel 443 changed after setup. The saved original route was not restored over later edits.'
                }
                # If a failed native mutation left the old route unchanged,
                # it is already restored and no further mutation is needed.
            }
            elseif ($scope.Present) {
                if (-not (Test-DesiredFunnel443 $scope)) { throw 'Funnel 443 changed after setup.' }
                $null = Invoke-Native -File $script:TailscaleExe -Arguments @('funnel', '--https=443', 'off') -Description 'Remove owned Funnel 443'
                if ((Get-Funnel443Scope (Get-FunnelConfiguration)).Present) { throw 'Funnel 443 is still configured.' }
            }
            $script:Journal.OwnsFunnel443 = $false
            Save-Journal
        }
        catch { $issues += 'Owned Funnel 443 could not be safely restored or removed; review the saved backup.' }
    }
    if ($script:Journal.OwnsPortproxy) {
        try {
            $rules = @(Get-Portproxy8380)
            if ($rules.Count -gt 0) {
                if (-not (Test-DesiredPortproxy $rules)) { throw 'Portproxy 8380 changed after setup.' }
                $null = Invoke-Native -File $script:NetshExe -Arguments @('interface', 'portproxy', 'delete', 'v4tov4', 'listenaddress=127.0.0.1', 'listenport=8380', 'protocol=tcp') -Description 'Remove owned loopback portproxy'
                if (@(Get-Portproxy8380).Count -gt 0) { throw 'Portproxy 8380 is still configured.' }
            }
            $script:Journal.OwnsPortproxy = $false
            Save-Journal
        }
        catch { $issues += 'Owned loopback portproxy could not be safely removed; review the saved backup.' }
    }
    $script:Journal.Active = ($script:Journal.OwnsFunnel443 -or $script:Journal.OwnsPortproxy)
    $script:Journal.LastAction = 'rollback'
    Save-Journal
    if ($script:Journal.IpHelperStarted) {
        Write-Host 'IP Helper was started by setup. It is left running because it is a shared Windows service.'
    }
    if ($issues.Count -gt 0) { throw ($issues -join ' ') }
}

$lockHandle = $null
try {
    if ($Rollback -and $MigrateLegacy8080) { throw 'Use either -Rollback or -MigrateLegacy8080, not both.' }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this saved script in Administrator PowerShell on hlq-1.'
    }
    try {
        $lockHandle = [System.IO.File]::Open($LockPath, [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    }
    catch { throw 'Another copy of this relay script is running, or its local folder is not writable.' }

    $tailscaleCommand = Get-Command 'tailscale.exe' -ErrorAction SilentlyContinue
    if ($null -ne $tailscaleCommand) { $script:TailscaleExe = $tailscaleCommand.Source }
    else { $script:TailscaleExe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe' }
    if (-not (Test-Path -LiteralPath $script:TailscaleExe -PathType Leaf)) { throw 'Tailscale is not installed.' }
    $script:NetshExe = Join-Path $env:SystemRoot 'System32\netsh.exe'
    if ((Get-Service -Name 'Tailscale').Status -ne 'Running') { throw 'Tailscale must already be running and connected on hlq-1.' }
    $statusJson = Invoke-Native -File $script:TailscaleExe -Arguments @('status', '--json') -Description 'Read local Tailscale identity'
    $status = $statusJson | ConvertFrom-Json
    $self = Get-PropertyValue $status 'Self'
    $actualDns = [string](Get-PropertyValue $self 'DNSName')
    if ((Get-PropertyValue $status 'BackendState') -cne 'Running' -or
        (Get-PropertyValue $self 'Online') -cne $true -or
        $actualDns.TrimEnd('.').ToLowerInvariant() -cne $ExpectedDns) {
        throw 'This is not the online hlq-1.tail50f4d4.ts.net node. No network setting was changed.'
    }
    # Do not retain the full tailnet status, peer list, or user identities.
    $statusJson = $null
    $status = $null
    $self = $null

    if (Test-Path -LiteralPath $StatePath) {
        try { $script:Journal = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json }
        catch { throw 'Ownership journal is unreadable. Review it manually; nothing was changed.' }
        Assert-JournalScope $script:Journal
    }
    if ($Rollback) {
        if ($null -eq $script:Journal) { throw 'No ownership journal exists beside this script. Nothing was changed.' }
        Undo-OwnedRoutes
        Write-Host 'Rollback complete. The original root proxy was restored if migrated; only routes created by this script were removed. Other ports were preserved.'
        exit 0
    }

    # Require the exact isolated backend before touching local networking.
    Assert-ReadyHealth $TargetUrl
    $funnelBefore = Get-Funnel443Scope (Get-FunnelConfiguration)
    $migrateLegacyRoute = $false
    if ($funnelBefore.Present -and -not (Test-DesiredFunnel443 $funnelBefore)) {
        if ($MigrateLegacy8080 -and (Test-LegacyFunnel443 $funnelBefore)) {
            $migrateLegacyRoute = $true
        }
        else {
            throw 'HTTPS/Funnel port 443 already has different configuration. Only the exact known hlq-1 root proxy to 127.0.0.1:8080 may be migrated with -MigrateLegacy8080; nothing was changed.'
        }
    }
    $rulesBefore = @(Get-Portproxy8380)
    if ($rulesBefore.Count -gt 0 -and -not (Test-DesiredPortproxy $rulesBefore)) {
        throw 'Portproxy port 8380 already has different configuration. Review it manually; nothing was changed.'
    }
    $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -eq $RelayPort })
    if ($listeners.Count -gt 0) {
        if (-not (Test-DesiredPortproxy $rulesBefore) -or
            @($listeners | Where-Object { $_.LocalAddress -cne $ListenAddress }).Count -gt 0) {
            throw 'TCP port 8380 already has a conflicting listener. Review it manually; nothing was changed.'
        }
        Assert-ReadyHealth $LocalUrl
    }
    $ipHelper = Get-Service -Name 'iphlpsvc'
    $ipHelperDetails = Get-CimInstance -ClassName Win32_Service -Filter "Name='iphlpsvc'"
    if ($ipHelperDetails.StartMode -eq 'Disabled') { throw 'IP Helper is disabled. Review it manually; nothing was changed.' }

    if ($null -ne $script:Journal -and $script:Journal.Active) {
        if (-not (Test-DesiredFunnel443 $funnelBefore) -or -not (Test-DesiredPortproxy $rulesBefore)) {
            throw 'The active relay differs from its ownership journal. Use -Rollback or review it manually first.'
        }
    }
    else {
        $backupDirectory = Join-Path $BackupRoot ((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
        $null = New-Item -ItemType Directory -Path $backupDirectory -Force
        Save-Json -Path (Join-Path $backupDirectory 'funnel-443-before.json') -Value $funnelBefore
        Save-Json -Path (Join-Path $backupDirectory 'portproxy-8380-before.json') -Value @($rulesBefore)
        $script:Journal = [pscustomobject]@{
            FormatVersion = 1
            ExpectedDns = $ExpectedDns
            TargetAddress = $TargetAddress
            RelayPort = $RelayPort
            LocalUrl = $LocalUrl
            ExpectedVersion = $ExpectedVersion
            BackupDirectory = $backupDirectory
            Funnel443WasPresent = [bool]$funnelBefore.Present
            PortproxyWasPresent = [bool]($rulesBefore.Count -gt 0)
            IpHelperBefore = [string]$ipHelper.Status
            IpHelperStarted = $false
            OwnsPortproxy = $false
            OwnsFunnel443 = $false
            RestoreLegacy8080 = [bool]$migrateLegacyRoute
            Active = $true
            LastAction = 'prepared'
        }
        Save-Journal
    }

    try {
        if ($ipHelper.Status -ne 'Running') {
            Write-Host 'Starting the existing Windows IP Helper service for the loopback relay.'
            Start-Service -Name 'iphlpsvc'
            $ipHelper.WaitForStatus('Running', [TimeSpan]::FromSeconds(15))
            $script:Journal.IpHelperStarted = $true
            Save-Journal
        }
        if ($rulesBefore.Count -eq 0) {
            # Persist ownership before the native mutation so a later invocation
            # can recover from an interrupted setup. No pre-existing rule is owned.
            $script:Journal.OwnsPortproxy = $true
            $script:Journal.LastAction = 'creating-portproxy'
            Save-Journal
            $null = Invoke-Native -File $script:NetshExe -Arguments @('interface', 'portproxy', 'add', 'v4tov4', 'listenaddress=127.0.0.1', 'listenport=8380', 'connectaddress=100.93.38.9', 'connectport=8380', 'protocol=tcp') -Description 'Create scoped loopback portproxy'
            if (-not (Test-DesiredPortproxy @(Get-Portproxy8380))) { throw 'The new portproxy did not match the requested route.' }
        }
        # Portproxy may need a moment after IP Helper starts. No Add is sent.
        $relayReady = $false
        for ($attempt = 0; $attempt -lt 5; $attempt++) {
            try { Assert-ReadyHealth $LocalUrl; $relayReady = $true; break }
            catch { if ($attempt -lt 4) { Start-Sleep -Milliseconds 500 } }
        }
        if (-not $relayReady) { throw 'The local relay health check did not pass.' }

        if ($migrateLegacyRoute) {
            $currentScope = Get-Funnel443Scope (Get-FunnelConfiguration)
            if (-not (Test-LegacyFunnel443 $currentScope)) {
                throw 'Funnel 443 changed during setup. Refusing to replace it.'
            }
            $script:Journal.OwnsFunnel443 = $true
            $script:Journal.LastAction = 'migrating-legacy-funnel'
            Save-Journal
            # Saving the journal can take time. Recheck the exact old scope
            # immediately before replacement; a different route is not owned.
            if (-not (Test-LegacyFunnel443 (Get-Funnel443Scope (Get-FunnelConfiguration)))) {
                $script:Journal.OwnsFunnel443 = $false
                Save-Journal
                throw 'Funnel 443 changed before migration. No Funnel route was replaced.'
            }
            $null = Invoke-Native -File $script:TailscaleExe -Arguments @('funnel', '--bg', '--https=443', '--yes', $LocalUrl) -Description 'Migrate this node HTTPS root proxy from port 8080 to 8380'
        }
        elseif (-not $funnelBefore.Present) {
            $currentScope = Get-Funnel443Scope (Get-FunnelConfiguration)
            if ($currentScope.Present) { throw 'Funnel 443 changed during setup. Refusing to overwrite it.' }
            $script:Journal.OwnsFunnel443 = $true
            $script:Journal.LastAction = 'creating-funnel'
            Save-Journal
            if ((Get-Funnel443Scope (Get-FunnelConfiguration)).Present) {
                $script:Journal.OwnsFunnel443 = $false
                Save-Journal
                throw 'Funnel 443 changed before creation. No Funnel route was created.'
            }
            $null = Invoke-Native -File $script:TailscaleExe -Arguments @('funnel', '--bg', '--https=443', '--yes', $LocalUrl) -Description 'Enable this node Funnel HTTPS 443'
        }
        if (-not (Test-DesiredFunnel443 (Get-Funnel443Scope (Get-FunnelConfiguration)))) {
            throw 'Funnel 443 did not match the requested route.'
        }
        Assert-ReadyHealth $LocalUrl
        $script:Journal.LastAction = 'ready'
        Save-Journal
    }
    catch {
        $setupError = $_.Exception.Message
        try { Undo-OwnedRoutes }
        catch { Write-Warning $_.Exception.Message }
        throw ($setupError + ' Newly owned routes were rolled back where safely possible. Backups were retained.')
    }

    Write-Host 'Relay ready: hlq-1 HTTPS 443 -> 127.0.0.1:8380 -> 100.93.38.9:8380 (textual v3.16.0, GPT Add configured).'
    Write-Host ('Health: https://' + $ExpectedDns + '/health')
    Write-Host ('Add:    https://' + $ExpectedDns + '/add')
    Write-Host ('Search: https://' + $ExpectedDns + '/search')
    Write-Host ('Saved backup: ' + $script:Journal.BackupDirectory)
    try {
        Assert-ReadyHealth ('https://' + $ExpectedDns)
        Write-Host 'HTTPS hostname health passed from this machine. Independent public-network verification is still required before Smoke.'
    }
    catch {
        Write-Warning 'The relay is configured, but HTTPS hostname health is not ready here. Public DNS can take up to 10 minutes; verify externally before Smoke.'
    }
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
finally {
    if ($null -ne $lockHandle) { $lockHandle.Dispose() }
}
