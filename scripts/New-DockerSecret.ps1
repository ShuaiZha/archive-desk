[CmdletBinding()]
param(
    [string]$OutputPath
)

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $PSScriptRoot "..\.docker\archivedesk_master_key"
}

$resolvedOutput = [IO.Path]::GetFullPath($OutputPath)
$parent = Split-Path -Parent $resolvedOutput

if (Test-Path -LiteralPath $resolvedOutput) {
    throw "Secret already exists at $resolvedOutput. Keep it to retain access to encrypted credentials."
}

New-Item -ItemType Directory -Path $parent -Force | Out-Null

$key = New-Object byte[] 32
$generator = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $generator.GetBytes($key)
}
finally {
    $generator.Dispose()
}

[IO.File]::WriteAllText(
    $resolvedOutput,
    [Convert]::ToBase64String($key),
    [Text.Encoding]::ASCII
)

Write-Output "Created Docker Secret at $resolvedOutput"
Write-Output "Back up this file securely. Do not commit or share it."
