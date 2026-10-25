param([Parameter(Mandatory)][string]$ReportDirectory)

$ErrorActionPreference = 'Stop'
$custom = @{}
$none = @{}
Get-Content -LiteralPath (Join-Path $ReportDirectory 'custom/files.json') -Raw |
    ConvertFrom-Json | ForEach-Object { $custom[$_.path] = $_.sha256 }
Get-Content -LiteralPath (Join-Path $ReportDirectory 'none/files.json') -Raw |
    ConvertFrom-Json | ForEach-Object { $none[$_.path] = $_.sha256 }
$paths = @(@($custom.Keys) + @($none.Keys) | Sort-Object -Unique)
$executables = @('TGFocus.exe', 'helper\focus-worker.exe')
$differences = @($paths | Where-Object { $custom[$_] -ne $none[$_] })
$unexpected = @($differences | Where-Object { $_ -notin $executables })
$report = [ordered]@{
    sourceCommit = $env:GITHUB_SHA
    sourceDateEpoch = $env:SOURCE_DATE_EPOCH
    pythonHashSeed = $env:PYTHONHASHSEED
    customFileCount = $custom.Count
    noneFileCount = $none.Count
    differentFiles = $differences
    unexpectedDifferentFiles = $unexpected
}
$report | ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath (Join-Path $ReportDirectory 'comparison.json') -Encoding utf8
$annotation = ($report | ConvertTo-Json -Depth 4 -Compress).Replace('%', '%25').Replace("`r", '%0D').Replace("`n", '%0A')
Write-Output "::notice title=Windows icon comparison::$annotation"
if ($unexpected.Count -gt 0) {
    throw 'Payloads differ outside the two EXEs; inspect comparison.json before interpreting the experiment'
}
foreach ($executable in $executables) {
    if (-not $custom.ContainsKey($executable) -or -not $none.ContainsKey($executable) -or
        $custom[$executable] -eq $none[$executable]) {
        throw "Missing or identical executable in the comparison: $executable"
    }
}
Write-Output 'All non-EXE files have identical SHA-256 hashes; both EXEs differ as expected.'
