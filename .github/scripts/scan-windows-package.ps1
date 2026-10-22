param(
    [Parameter(Mandatory)][string]$ArtifactDirectory,
    [Parameter(Mandatory)][string]$ReportDirectory
)

# Diagnose our existing CI package. Never change Defender preferences, add
# exclusions, execute the downloaded app, or submit samples to another service.
$ErrorActionPreference = 'Stop'
$reportRoot = (New-Item -ItemType Directory -Force -Path $ReportDirectory).FullName
$report = [ordered]@{
    status = 'incomplete'
    startedUtc = [DateTime]::UtcNow.ToString('o')
    sourceRun = $env:TGFOCUS_SOURCE_RUN
    scope = 'Inner ZIP and extracted payload; Microsoft Defender only, not Chrome Safe Browsing'
}

try {
    $artifactRoot = (Resolve-Path -LiteralPath $ArtifactDirectory).Path
    $archive = Join-Path $artifactRoot 'TGFocus-Windows-x64.zip'
    $checksum = (Get-Content -LiteralPath "$archive.sha256" -Raw).Trim()
    if ($checksum -notmatch '^([0-9a-fA-F]{64})  TGFocus-Windows-x64\.zip$') {
        throw 'Unexpected archive checksum format'
    }
    $expectedHash = $Matches[1].ToLowerInvariant()
    $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    $report.archiveSha256 = $actualHash
    if ($expectedHash -ne $actualHash) {
        throw 'Archive SHA-256 does not match the accompanying checksum'
    }

    $expanded = Join-Path $artifactRoot 'expanded'
    Expand-Archive -LiteralPath $archive -DestinationPath $expanded
    $inventory = @(Get-ChildItem -LiteralPath $expanded -Recurse -File | ForEach-Object {
        [pscustomobject]@{
            path = [IO.Path]::GetRelativePath($expanded, $_.FullName)
            bytes = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    })
    $inventory | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $reportRoot 'files.json') -Encoding utf8
    $report.fileCount = $inventory.Count

    $defender = Get-MpComputerStatus
    $report.defender = $defender | Select-Object AMServiceEnabled, AntivirusEnabled, AMRunningMode,
        AMEngineVersion, AMProductVersion, AntivirusSignatureVersion, AntivirusSignatureLastUpdated
    if (-not $defender.AMServiceEnabled -or -not $defender.AntivirusEnabled) {
        $report.status = 'scanner_unavailable'
        throw 'Defender is not enabled on this runner; no clean scan is claimed'
    }
    # Hosted images may have older definitions. Use the normal Defender update
    # mechanism; failure must not be presented as a current-signature scan.
    $report.signatureUpdate = 'incomplete'
    Update-MpSignature -ErrorAction Stop
    $report.signatureUpdate = 'successful'
    $defender = Get-MpComputerStatus
    $report.defender = $defender | Select-Object AMServiceEnabled, AntivirusEnabled, AMRunningMode,
        AMEngineVersion, AMProductVersion, AntivirusSignatureVersion, AntivirusSignatureLastUpdated
    $platform = Join-Path $env:ProgramData 'Microsoft\Windows Defender\Platform'
    $scanner = Get-ChildItem -Path (Join-Path $platform '*/MpCmdRun.exe') -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    $scannerPath = if ($scanner) { $scanner.FullName } else {
        Join-Path $env:ProgramFiles 'Windows Defender\MpCmdRun.exe'
    }
    if (-not (Test-Path -LiteralPath $scannerPath -PathType Leaf)) {
        $report.status = 'scanner_unavailable'
        throw 'MpCmdRun.exe is unavailable; no clean scan is claimed'
    }
    & $scannerPath -? 2>&1 | Set-Content -LiteralPath (Join-Path $reportRoot 'defender-help.txt') -Encoding utf8
    # A custom scan with remediation disabled preserves the original evidence.
    # A nonzero result is not automatically classified as a malware finding.
    & $scannerPath -Scan -ScanType 3 -File $artifactRoot -DisableRemediation 2>&1 |
        Tee-Object -FilePath (Join-Path $reportRoot 'defender.txt')
    $scanExitCode = $LASTEXITCODE
    $report.scanExitCode = $scanExitCode
    if ($scanExitCode -ne 0) {
        $report.status = 'scan_requires_investigation'
        throw "Defender returned $scanExitCode; inspect defender.txt for detection versus scan failure"
    }
    $report.status = 'no_threats_reported'
    Write-Output 'Defender reported no threats. This does not establish the Chrome download verdict.'
} catch {
    $report.error = $_.Exception.Message
    throw
} finally {
    $report.finishedUtc = [DateTime]::UtcNow.ToString('o')
    $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $reportRoot 'report.json') -Encoding utf8
    # Keep the compact verdict accessible through the Checks API as well as
    # downloadable logs; no credentials or message data enter this workflow.
    $annotation = ($report | ConvertTo-Json -Depth 6 -Compress).Replace('%', '%25').Replace("`r", '%0D').Replace("`n", '%0A')
    Write-Output "::notice title=Windows artifact scan::$annotation"
    if ($env:GITHUB_STEP_SUMMARY) {
        @(
            '### Existing Windows artifact: Defender check'
            ''
            "Status: ``$($report.status)``"
            "Archive SHA-256: ``$($report.archiveSha256)``"
            ''
            'See the security-report artifact for scanner versions, output and file hashes.'
            'A successful scan is not a Chrome Safe Browsing clearance or a guarantee of safety.'
        ) | Add-Content -LiteralPath $env:GITHUB_STEP_SUMMARY
    }
}
