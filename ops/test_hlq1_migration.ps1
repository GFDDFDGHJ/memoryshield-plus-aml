#requires -Version 5.1
# Offline tests only. Parse selected relay functions/blocks without running setup.
[CmdletBinding()]
param([string]$SourcePath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($SourcePath)) {
    $SourcePath = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'configure-hlq1-ingress.ps1'
}
$tokens = $null
$parseErrors = $null
$sourceAst = [System.Management.Automation.Language.Parser]::ParseFile($SourcePath, [ref]$tokens, [ref]$parseErrors)
if (@($parseErrors).Count -ne 0) { throw 'Relay script did not parse.' }
$functionNames = @('Get-PropertyValue', 'Get-Funnel443Scope', 'Test-ExactFunnel443', 'Test-DesiredFunnel443', 'Test-LegacyFunnel443', 'Test-DesiredPortproxy', 'Assert-JournalScope', 'Undo-OwnedRoutes')
foreach ($functionName in $functionNames) {
    $matching = @($sourceAst.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $functionName }, $true))
    if ($matching.Count -ne 1) { throw ('Expected exactly one function: ' + $functionName) }
    . ([scriptblock]::Create($matching[0].Extent.Text))
}
$guardAst = @($sourceAst.FindAll({ param($node) $node -is [System.Management.Automation.Language.IfStatementAst] -and $node.Extent.Text.StartsWith('if ($funnelBefore.Present -and -not (Test-DesiredFunnel443 $funnelBefore))') }, $true))
if ($guardAst.Count -ne 1) { throw 'Migration authorization guard was not found exactly once.' }
$migrationGuard = [scriptblock]::Create($guardAst[0].Extent.Text)
$setupAst = @($sourceAst.FindAll({ param($node) $node -is [System.Management.Automation.Language.TryStatementAst] -and $node.Body.Extent.Text.TrimStart().StartsWith("{`n        if (`$ipHelper.Status") }, $true))
# ParseFile preserves CRLF in a saved Windows script.
if ($setupAst.Count -ne 1) {
    $setupAst = @($sourceAst.FindAll({ param($node) $node -is [System.Management.Automation.Language.TryStatementAst] -and ($node.Body.Extent.Text -replace "`r`n", "`n").TrimStart().StartsWith("{`n        if (`$ipHelper.Status") }, $true))
}
if ($setupAst.Count -ne 1) { throw 'Owned mutation transaction was not found exactly once.' }
$ownedSetup = [scriptblock]::Create($setupAst[0].Extent.Text)

$ExpectedDns = 'hlq-1.tail50f4d4.ts.net'
$FunnelKey = $ExpectedDns + ':443'
$TargetAddress = '100.93.38.9'
$RelayPort = 8380
$LocalUrl = 'http://127.0.0.1:8380'
$LegacyLocalUrl = 'http://127.0.0.1:8080'
$MappingName = '127.0.0.1/8380'
$MappingValue = '100.93.38.9/8380'
$script:TailscaleExe = 'MOCK-TAILSCALE'
$script:NetshExe = 'MOCK-NETSH'
$script:CaseCount = 0

function Assert-True { param($Value, [string]$Message) if (-not $Value) { throw $Message } }
function Assert-False { param($Value, [string]$Message) if ($Value) { throw $Message } }
function Assert-Equal { param($Actual, $Expected, [string]$Message) if ($Actual -cne $Expected) { throw ($Message + ': expected ' + $Expected + ', got ' + $Actual) } }
function Assert-Throws {
    param([scriptblock]$Action, [string]$MessagePattern)
    $caught = $false
    try { & $Action }
    catch { $caught = $true; if ($_.Exception.Message -notmatch $MessagePattern) { throw ('Unexpected error: ' + $_.Exception.Message) } }
    if (-not $caught) { throw 'Expected operation to fail.' }
}
function Test-Case {
    param([string]$Name, [scriptblock]$Action)
    & $Action
    $script:CaseCount++
    Write-Host ('PASS ' + $Name)
}
function New-Fixture {
    param([string]$Proxy = $LegacyLocalUrl)
    $fixture = @{
        TCP = @{ '443' = @{ HTTPS = $true }; '8443' = @{ HTTPS = $true } }
        Web = @{ $FunnelKey = @{ Handlers = @{ '/' = @{ Proxy = $Proxy } } }; 'hlq-1.tail50f4d4.ts.net:8443' = @{ Handlers = @{ '/' = @{ Proxy = 'http://127.0.0.1:9999' } } } }
        AllowFunnel = @{ $FunnelKey = $true; 'hlq-1.tail50f4d4.ts.net:8443' = $true }
    }
    return ($fixture | ConvertTo-Json -Depth 20 | ConvertFrom-Json)
}
function New-Journal {
    param([bool]$OwnsFunnel = $true, [bool]$OwnsPort = $true, [bool]$RestoreLegacy = $true)
    return [pscustomobject]@{
        FormatVersion = 1; ExpectedDns = $ExpectedDns; TargetAddress = $TargetAddress
        RelayPort = $RelayPort; LocalUrl = $LocalUrl; Active = $true
        OwnsPortproxy = $OwnsPort; OwnsFunnel443 = $OwnsFunnel
        IpHelperStarted = $false; RestoreLegacy8080 = $RestoreLegacy; LastAction = 'ready'
    }
}
function Reset-Mocks {
    param([string]$Proxy = $LocalUrl, [bool]$PortPresent = $true)
    $script:Configuration = New-Fixture $Proxy
    $script:Rules = @()
    if ($PortPresent) { $script:Rules = @([pscustomobject]@{ Kind = 'v4tov4'; Listen = $MappingName; Connect = $MappingValue }) }
    $script:Calls = @()
    $script:Saves = @()
    $script:HealthCalls = 0
    $script:FailHealthAt = 0
    $script:FailNativeBeforeMutation = $false
    $script:FailNativeAfterMutation = $false
    $script:EditFunnelAtSaveAction = ''
}
function Get-FunnelConfiguration { return $script:Configuration }
function Get-Portproxy8380 { return $script:Rules }
function Save-Journal {
    $script:Saves += ($script:Journal | ConvertTo-Json -Compress -Depth 10)
    if ($script:EditFunnelAtSaveAction -and $script:Journal.LastAction -ceq $script:EditFunnelAtSaveAction) {
        $script:Configuration = New-Fixture 'http://127.0.0.1:9000'
        $script:EditFunnelAtSaveAction = ''
    }
}
function Assert-ReadyHealth {
    param([string]$Url)
    $script:HealthCalls++
    if ($script:FailHealthAt -eq $script:HealthCalls) { throw 'MOCK final health failure' }
}
function Invoke-Native {
    param([string]$File, [string[]]$Arguments, [string]$Description)
    $script:Calls += [pscustomobject]@{ File = $File; Arguments = $Arguments; Description = $Description }
    if ($script:FailNativeBeforeMutation) { $script:FailNativeBeforeMutation = $false; throw 'MOCK native failed before mutation' }
    if ($File -ceq $script:TailscaleExe) {
        if ($Arguments[0] -cne 'funnel') { throw 'Unexpected mocked Tailscale operation.' }
        if ($Arguments.Count -eq 3 -and $Arguments[1] -ceq '--https=443' -and $Arguments[2] -ceq 'off') {
            $script:Configuration.TCP.PSObject.Properties.Remove('443')
            $script:Configuration.Web.PSObject.Properties.Remove($FunnelKey)
            $script:Configuration.AllowFunnel.PSObject.Properties.Remove($FunnelKey)
        }
        elseif ($Arguments.Count -eq 5 -and $Arguments[1] -ceq '--bg' -and $Arguments[2] -ceq '--https=443' -and $Arguments[3] -ceq '--yes' -and $Arguments[4] -cin @($LocalUrl, $LegacyLocalUrl)) {
            if (-not $script:Journal.OwnsFunnel443) { throw 'Mutation was attempted without durable ownership.' }
            if ($script:Saves.Count -eq 0) { throw 'Mutation was attempted before saving ownership.' }
            $script:Configuration = New-Fixture $Arguments[4]
        }
        else { throw ('Unexpected mocked Tailscale arguments: ' + ($Arguments -join ' ')) }
    }
    elseif ($File -ceq $script:NetshExe) {
        if (($Arguments -join ' ') -ceq 'interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=8380 protocol=tcp') { $script:Rules = @() }
        elseif (($Arguments -join ' ') -ceq 'interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=8380 connectaddress=100.93.38.9 connectport=8380 protocol=tcp') {
            if (-not $script:Journal.OwnsPortproxy -or $script:Saves.Count -eq 0) { throw 'Portproxy mutation attempted before saved ownership.' }
            $script:Rules = @([pscustomobject]@{ Kind = 'v4tov4'; Listen = $MappingName; Connect = $MappingValue })
        }
        else { throw 'Unexpected mocked netsh operation.' }
    }
    else { throw 'Native process execution is forbidden in offline tests.' }
    if ($script:FailNativeAfterMutation) { $script:FailNativeAfterMutation = $false; throw 'MOCK native failed after mutation' }
    return ''
}

Test-Case 'accept exact legacy 8080 and ignore unrelated 8443' {
    Assert-True (Test-LegacyFunnel443 (Get-Funnel443Scope (New-Fixture))) 'Exact old route rejected.'
}
Test-Case 'accept exact desired 8380' {
    Assert-True (Test-DesiredFunnel443 (Get-Funnel443Scope (New-Fixture $LocalUrl))) 'Exact new route rejected.'
}
Test-Case 'reject another host on HTTPS 443' {
    $config = New-Fixture
    $value = $config.Web.$FunnelKey
    $config.Web.PSObject.Properties.Remove($FunnelKey)
    $config.Web | Add-Member -NotePropertyName 'other.tail50f4d4.ts.net:443' -NotePropertyValue $value
    Assert-False (Test-LegacyFunnel443 (Get-Funnel443Scope $config)) 'Wrong host accepted.'
}
Test-Case 'reject different proxy target' {
    Assert-False (Test-LegacyFunnel443 (Get-Funnel443Scope (New-Fixture 'http://127.0.0.1:8081'))) 'Wrong target accepted.'
}
Test-Case 'reject case or slash variants of target' {
    Assert-False (Test-LegacyFunnel443 (Get-Funnel443Scope (New-Fixture 'http://127.0.0.1:8080/'))) 'Trailing slash variant accepted.'
    Assert-False (Test-LegacyFunnel443 (Get-Funnel443Scope (New-Fixture 'HTTP://127.0.0.1:8080'))) 'Case variant accepted.'
}
Test-Case 'reject extra handler' {
    $config = New-Fixture
    $config.Web.$FunnelKey.Handlers | Add-Member -NotePropertyName '/other' -NotePropertyValue ([pscustomobject]@{ Proxy = 'http://127.0.0.1:1234' })
    Assert-False (Test-LegacyFunnel443 (Get-Funnel443Scope $config)) 'Extra handler accepted.'
}
Test-Case 'reject foreground 443' {
    $config = New-Fixture
    $config | Add-Member -NotePropertyName Foreground -NotePropertyValue ([pscustomobject]@{ session = (New-Fixture) })
    Assert-False (Test-LegacyFunnel443 (Get-Funnel443Scope $config)) 'Foreground route accepted.'
}
Test-Case 'reject extra TCP property and string HTTPS or AllowFunnel' {
    $config = New-Fixture
    $config.TCP.'443' | Add-Member -NotePropertyName TCPForward -NotePropertyValue '127.0.0.1:22'
    Assert-False (Test-LegacyFunnel443 (Get-Funnel443Scope $config)) 'Extra TCP property accepted.'
    $config = New-Fixture
    $config.TCP.'443'.HTTPS = 'true'
    Assert-False (Test-LegacyFunnel443 (Get-Funnel443Scope $config)) 'String HTTPS accepted.'
    $config = New-Fixture
    $config.AllowFunnel.$FunnelKey = 'true'
    Assert-False (Test-LegacyFunnel443 (Get-Funnel443Scope $config)) 'String AllowFunnel accepted.'
}
Test-Case 'deny legacy migration by default' {
    $funnelBefore = Get-Funnel443Scope (New-Fixture)
    $MigrateLegacy8080 = $false
    $migrateLegacyRoute = $false
    Assert-Throws { . $migrationGuard } 'different configuration'
    Assert-False $migrateLegacyRoute 'Default guard authorized migration.'
}
Test-Case 'authorize exact legacy only with migration flag' {
    $funnelBefore = Get-Funnel443Scope (New-Fixture)
    $MigrateLegacy8080 = $true
    $migrateLegacyRoute = $false
    . $migrationGuard
    Assert-True $migrateLegacyRoute 'Explicit migration was not selected.'
}
Test-Case 'migration flag still denies unknown route' {
    $funnelBefore = Get-Funnel443Scope (New-Fixture 'http://127.0.0.1:9000')
    $MigrateLegacy8080 = $true
    $migrateLegacyRoute = $false
    Assert-Throws { . $migrationGuard } 'different configuration'
}
Test-Case 'desired route needs no migration authorization' {
    $funnelBefore = Get-Funnel443Scope (New-Fixture $LocalUrl)
    $MigrateLegacy8080 = $false
    $migrateLegacyRoute = $false
    . $migrationGuard
    Assert-False $migrateLegacyRoute 'Pre-existing desired route marked as migrated.'
}
Test-Case 'old format-1 journal normalizes missing restore flag' {
    $journal = New-Journal $true $true $false
    $journal.PSObject.Properties.Remove('RestoreLegacy8080')
    Assert-JournalScope $journal
    Assert-Equal $journal.RestoreLegacy8080 $false 'Old journal gained restore authorization'
}
Test-Case 'journal rejects string restore flag' {
    $journal = New-Journal
    $journal.RestoreLegacy8080 = 'true'
    Assert-Throws { Assert-JournalScope $journal } 'invalid migration flag'
}
Test-Case 'journal rejects another target before rollback' {
    $journal = New-Journal
    $journal.TargetAddress = '100.1.2.3'
    Assert-Throws { Assert-JournalScope $journal } 'different scope'
}
Test-Case 'rollback restores old root and deletes only owned loopback rule' {
    Reset-Mocks
    $script:Journal = New-Journal
    Save-Journal
    Undo-OwnedRoutes
    Assert-True (Test-LegacyFunnel443 (Get-Funnel443Scope $script:Configuration)) 'Original route was not restored.'
    Assert-Equal $script:Rules.Count 0 'Owned portproxy retained'
    Assert-Equal $script:Calls.Count 2 'Expected only scoped restore and scoped delete'
    Assert-Equal $script:Configuration.Web.'hlq-1.tail50f4d4.ts.net:8443'.Handlers.'/'.Proxy 'http://127.0.0.1:9999' 'Other port changed'
    Assert-False $script:Journal.Active 'Rollback did not relinquish ownership.'
}
Test-Case 'fresh owned Funnel rollback removes only 443' {
    Reset-Mocks
    $script:Journal = New-Journal $true $false $false
    Save-Journal
    Undo-OwnedRoutes
    Assert-False (Get-Funnel443Scope $script:Configuration).Present 'Owned 443 retained.'
    Assert-True ($null -ne $script:Configuration.TCP.'8443') 'Unrelated 8443 removed.'
    Assert-Equal $script:Rules.Count 1 'Unowned portproxy deleted'
    Assert-Equal $script:Calls.Count 1 'Unexpected extra rollback mutation'
}
Test-Case 'unowned desired routes remain intact' {
    Reset-Mocks
    $script:Journal = New-Journal $false $false $false
    Undo-OwnedRoutes
    Assert-True (Test-DesiredFunnel443 (Get-Funnel443Scope $script:Configuration)) 'Unowned Funnel changed.'
    Assert-Equal $script:Rules.Count 1 'Unowned portproxy changed'
    Assert-Equal $script:Calls.Count 0 'Unowned route was mutated'
}
Test-Case 'later admin Funnel edit protected and owned loopback removed' {
    Reset-Mocks 'http://127.0.0.1:9000'
    $script:Journal = New-Journal
    Save-Journal
    Assert-Throws { Undo-OwnedRoutes } 'could not be safely restored'
    Assert-Equal $script:Configuration.Web.$FunnelKey.Handlers.'/'.Proxy 'http://127.0.0.1:9000' 'Later root edit overwritten'
    Assert-Equal $script:Rules.Count 0 'Independent owned loopback rollback not attempted'
    Assert-True $script:Journal.OwnsFunnel443 'Protected route ownership was discarded.'
}
Test-Case 'later portproxy edit protected while root restored' {
    Reset-Mocks
    $script:Rules[0].Connect = '100.1.2.3/8380'
    $script:Journal = New-Journal
    Save-Journal
    Assert-Throws { Undo-OwnedRoutes } 'could not be safely removed'
    Assert-True (Test-LegacyFunnel443 (Get-Funnel443Scope $script:Configuration)) 'Safe root restore failed.'
    Assert-Equal $script:Rules[0].Connect '100.1.2.3/8380' 'Later portproxy edit overwritten'
    Assert-True $script:Journal.OwnsPortproxy 'Protected portproxy ownership discarded.'
}
Test-Case 'missing migrated root is not replaced on rollback' {
    Reset-Mocks
    $script:Configuration = [pscustomobject]@{}
    $script:Journal = New-Journal $true $false $true
    Assert-Throws { Undo-OwnedRoutes } 'could not be safely restored'
    Assert-Equal $script:Calls.Count 0 'Missing root was recreated without ownership evidence'
}
Test-Case 'already restored root is recognized without native mutation' {
    Reset-Mocks $LegacyLocalUrl $false
    $script:Journal = New-Journal $true $false $true
    Undo-OwnedRoutes
    Assert-Equal $script:Calls.Count 0 'Already restored route was mutated'
    Assert-False $script:Journal.OwnsFunnel443 'Restored root ownership retained.'
}
Test-Case 'failed final health rolls back completed migration transaction' {
    Reset-Mocks $LegacyLocalUrl $false
    $script:Journal = New-Journal $false $false $true
    $script:FailHealthAt = 2
    $ipHelper = [pscustomobject]@{ Status = 'Running' }
    $rulesBefore = @()
    $funnelBefore = Get-Funnel443Scope $script:Configuration
    $migrateLegacyRoute = $true
    Assert-Throws { . $ownedSetup } 'MOCK final health failure.*rolled back'
    Assert-True (Test-LegacyFunnel443 (Get-Funnel443Scope $script:Configuration)) 'Failed setup left new root installed.'
    Assert-Equal $script:Rules.Count 0 'Failed setup left new portproxy installed'
    Assert-Equal $script:Calls.Count 4 'Expected create portproxy, migrate, restore root, delete portproxy'
    Assert-False $script:Journal.Active 'Failed setup retained active ownership.'
}
Test-Case 'failed native migration before mutation keeps original route' {
    Reset-Mocks $LegacyLocalUrl $true
    $script:Journal = New-Journal $false $false $true
    $script:FailNativeBeforeMutation = $true
    $ipHelper = [pscustomobject]@{ Status = 'Running' }
    $rulesBefore = @($script:Rules)
    $funnelBefore = Get-Funnel443Scope $script:Configuration
    $migrateLegacyRoute = $true
    Assert-Throws { . $ownedSetup } 'MOCK native failed before mutation.*rolled back'
    Assert-True (Test-LegacyFunnel443 (Get-Funnel443Scope $script:Configuration)) 'Failed native call overwrote original.'
    Assert-Equal $script:Rules.Count 1 'Pre-existing unowned portproxy removed'
    Assert-Equal $script:Calls.Count 1 'Unnecessary restoration after unchanged failed mutation'
}
Test-Case 'native failure after migration is recovered from ownership journal' {
    Reset-Mocks $LegacyLocalUrl $true
    $script:Journal = New-Journal $false $false $true
    $script:FailNativeAfterMutation = $true
    $ipHelper = [pscustomobject]@{ Status = 'Running' }
    $rulesBefore = @($script:Rules)
    $funnelBefore = Get-Funnel443Scope $script:Configuration
    $migrateLegacyRoute = $true
    Assert-Throws { . $ownedSetup } 'MOCK native failed after mutation.*rolled back'
    Assert-True (Test-LegacyFunnel443 (Get-Funnel443Scope $script:Configuration)) 'Partial native mutation not restored.'
    Assert-Equal $script:Rules.Count 1 'Unowned pre-existing portproxy removed'
    Assert-Equal $script:Calls.Count 2 'Expected failed migration and exact original restore'
}
Test-Case 'admin edit immediately before migration mutation is preserved' {
    Reset-Mocks $LegacyLocalUrl $false
    $script:Journal = New-Journal $false $false $true
    $script:EditFunnelAtSaveAction = 'migrating-legacy-funnel'
    $ipHelper = [pscustomobject]@{ Status = 'Running' }
    $rulesBefore = @()
    $funnelBefore = Get-Funnel443Scope $script:Configuration
    $migrateLegacyRoute = $true
    Assert-Throws { . $ownedSetup } 'changed before migration.*rolled back'
    Assert-Equal $script:Configuration.Web.$FunnelKey.Handlers.'/'.Proxy 'http://127.0.0.1:9000' 'Immediate admin edit overwritten'
    Assert-Equal $script:Rules.Count 0 'Newly owned portproxy not rolled back'
    Assert-Equal $script:Calls.Count 2 'Only portproxy create/delete should have occurred'
    Assert-False $script:Journal.OwnsFunnel443 'An administrator edit was adopted.'
}
Test-Case 'admin new route immediately before fresh mutation is preserved' {
    Reset-Mocks $LocalUrl $true
    $script:Configuration = [pscustomobject]@{}
    $script:Journal = New-Journal $false $false $false
    $script:EditFunnelAtSaveAction = 'creating-funnel'
    $ipHelper = [pscustomobject]@{ Status = 'Running' }
    $rulesBefore = @($script:Rules)
    $funnelBefore = Get-Funnel443Scope $script:Configuration
    $migrateLegacyRoute = $false
    Assert-Throws { . $ownedSetup } 'changed before creation.*rolled back'
    Assert-Equal $script:Configuration.Web.$FunnelKey.Handlers.'/'.Proxy 'http://127.0.0.1:9000' 'Immediate new admin route overwritten'
    Assert-Equal $script:Rules.Count 1 'Pre-existing unowned portproxy removed'
    Assert-Equal $script:Calls.Count 0 'Native mutation occurred after detecting admin edit'
    Assert-False $script:Journal.OwnsFunnel443 'An administrator route was adopted.'
}

Write-Host ('Offline migration tests passed: ' + $script:CaseCount + '. No setup main, native executable, network, registry or production data was used.')
