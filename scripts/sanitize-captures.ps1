param(
    [string]$Source = "local_files/raw_captures",
    [string]$Out = "captures"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Is-ExcludedPath {
    param([string]$RelPath)

    $p = $RelPath -replace "\\", "/"

    if ($p -match "(^|/)FS(/|$)") { return $true }
    if ($p -match "(^|/)proto(/|$)") { return $true }
    if ($p -match "(^|/)bugreport_extract(/|$)") { return $true }
    if ($p -match "(^|/)new_capture_0518(/|$)") { return $true }
    if ($p -match "(^|/)new_log_0517(/|$)") { return $true }
    if ($p -match "(^|/)new_log_0517b(/|$)") { return $true }
    if ($p -match "btsnoop_hci\.log(\.last)?$") { return $true }

    return $false
}

function Is-ExcludedExtension {
    param([string]$Name)

    $lower = $Name.ToLowerInvariant()
    $binaryExt = @(
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".jpe",
        ".bin", ".pb", ".pcapng", ".gz", ".zip", ".7z", ".rar"
    )
    foreach ($ext in $binaryExt) {
        if ($lower.EndsWith($ext)) { return $true }
    }
    return $false
}

function Try-ReadText {
    param([string]$Path)

    try {
        return [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    }
    catch {
        try {
            return [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::GetEncoding(1252))
        }
        catch {
            return $null
        }
    }
}

function Redact-Content {
    param([string]$Text)

    $t = $Text

    # MAC addresses
    $t = [regex]::Replace($t, "(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b", "<REDACTED_MAC>")

    # Instax device name serial suffix (e.g. INSTAX-3332137670)
    $t = [regex]::Replace($t, "(?i)\b(INSTAX-)\d{5,}\b", '$1<REDACTED_SERIAL>')

    # Serial-number labels
    $t = [regex]::Replace($t, "(?im)(serial(?:\s*number)?\s*[:=]\s*)([A-Za-z0-9_-]+)", '$1<REDACTED_SERIAL>')

    # Common Android identifiers
    $t = [regex]::Replace($t, "(?im)\b(android[_-]?id|imei|meid|imsi|iccid|subscriber[_-]?id)\b\s*[:=]\s*[^\r\n]+", '$1: <REDACTED>')

    # Emails
    $t = [regex]::Replace($t, "(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "<REDACTED_EMAIL>")

    return $t
}

$srcFull = (Resolve-Path $Source).Path
$outFull = Join-Path (Get-Location) $Out

if (Test-Path $outFull) {
    Remove-Item -Recurse -Force $outFull
}
New-Item -ItemType Directory -Path $outFull | Out-Null

$included = New-Object System.Collections.Generic.List[string]
$skipped = New-Object System.Collections.Generic.List[string]

$files = Get-ChildItem -Path $srcFull -Recurse -File

foreach ($f in $files) {
    $rel = $f.FullName.Substring($srcFull.Length).TrimStart('\\')

    if (Is-ExcludedPath $rel) {
        $skipped.Add("EXCLUDED_PATH  $rel") | Out-Null
        continue
    }

    if (Is-ExcludedExtension $f.Name) {
        $skipped.Add("EXCLUDED_EXT   $rel") | Out-Null
        continue
    }

    $text = Try-ReadText $f.FullName
    if ($null -eq $text) {
        $skipped.Add("NON_TEXT       $rel") | Out-Null
        continue
    }

    $redacted = Redact-Content $text

    $dst = Join-Path $outFull $rel
    $dstDir = Split-Path -Parent $dst
    if (-not (Test-Path $dstDir)) {
        New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
    }

    [System.IO.File]::WriteAllText($dst, $redacted, [System.Text.Encoding]::UTF8)
    $included.Add($rel) | Out-Null
}

$manifest = @()
$manifest += "# Share-safe captures manifest"
$manifest += ""
$manifest += "Source: $srcFull"
$manifest += "Output: $outFull"
$manifest += ""
$manifest += "Included files: $($included.Count)"
$manifest += "Skipped files: $($skipped.Count)"
$manifest += ""
$manifest += "## Included"
$manifest += $included | Sort-Object
$manifest += ""
$manifest += "## Skipped"
$manifest += $skipped | Sort-Object

$manifestPath = Join-Path $outFull "SANITIZE_MANIFEST.md"
[System.IO.File]::WriteAllLines($manifestPath, $manifest, [System.Text.Encoding]::UTF8)

Write-Host "Sanitization complete."
Write-Host "  Included: $($included.Count)"
Write-Host "  Skipped:  $($skipped.Count)"
Write-Host "  Output:   $outFull"
Write-Host "  Manifest: $manifestPath"
