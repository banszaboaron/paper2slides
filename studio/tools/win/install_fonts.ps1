# Install every font in ..\..\fonts for the current user (no admin needed). Restart PowerPoint afterwards.
# The registry name is read straight from each file's OpenType 'name' table (full name, nameID 4),
# so no WPF GlyphTypeface is involved (it threw on some files and the stale value leaked into the next font).
$ErrorActionPreference = "Stop"
$src = Join-Path $PSScriptRoot "..\..\fonts"
$dst = Join-Path $env:LOCALAPPDATA "Microsoft\Windows\Fonts"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
$reg = "HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts"
if (-not (Test-Path $reg)) { New-Item -Path $reg -Force | Out-Null }

function Read-U16([byte[]]$b, [int]$o) { return ([int]$b[$o] -shl 8) -bor [int]$b[$o + 1] }
function Read-U32([byte[]]$b, [int]$o) { return ([long](Read-U16 $b $o) -shl 16) -bor [long](Read-U16 $b ($o + 2)) }

function Get-FontFullName([string]$path) {
    # Returns the full font name (nameID 4); Windows/English record preferred, Mac Roman as fallback.
    $b = [System.IO.File]::ReadAllBytes($path)
    $numTables = Read-U16 $b 4
    $nameOff = -1
    for ($i = 0; $i -lt $numTables; $i++) {
        $rec = 12 + 16 * $i
        $tag = [System.Text.Encoding]::ASCII.GetString($b, $rec, 4)
        if ($tag -eq "name") { $nameOff = [int](Read-U32 $b ($rec + 8)); break }
    }
    if ($nameOff -lt 0) { return $null }
    $count = Read-U16 $b ($nameOff + 2)
    $strBase = $nameOff + (Read-U16 $b ($nameOff + 4))
    $best = $null; $bestScore = -1
    for ($i = 0; $i -lt $count; $i++) {
        $r = $nameOff + 6 + 12 * $i
        $platform = Read-U16 $b $r
        $lang = Read-U16 $b ($r + 4)
        $nameId = Read-U16 $b ($r + 6)
        if ($nameId -ne 4) { continue }
        $len = Read-U16 $b ($r + 8)
        $off = $strBase + (Read-U16 $b ($r + 10))
        $score = -1
        if ($platform -eq 3 -and $lang -eq 0x409) { $score = 3 }
        elseif ($platform -eq 3) { $score = 2 }
        elseif ($platform -eq 1) { $score = 1 }
        if ($score -le $bestScore) { continue }
        if ($platform -eq 3) { $s = [System.Text.Encoding]::BigEndianUnicode.GetString($b, $off, $len) }
        else { $s = [System.Text.Encoding]::ASCII.GetString($b, $off, $len) }
        $best = $s; $bestScore = $score
    }
    return $best
}

$installed = 0; $failed = 0
Get-ChildItem $src -Filter *.ttf | ForEach-Object {
    $file = $_
    $target = Join-Path $dst $file.Name
    try {
        $full = Get-FontFullName $file.FullName
        if (-not $full) { $full = $file.BaseName }      # never reuse a previous font's name
        $name = "$full (TrueType)"
        Copy-Item $file.FullName $target -Force
        # Remove stale entries that point at this file under another name (left by the old script).
        $props = Get-ItemProperty -Path $reg
        foreach ($p in $props.PSObject.Properties) {
            if ($p.Name -like "PS*") { continue }
            if (($p.Value -eq $target) -and ($p.Name -ne $name)) {
                Remove-ItemProperty -Path $reg -Name $p.Name
                Write-Host "  removed stale entry '$($p.Name)'"
            }
        }
        New-ItemProperty -Path $reg -Name $name -Value $target -PropertyType String -Force | Out-Null
        Write-Host "installed $($file.Name) as $name"
        $installed++
    } catch {
        Write-Warning "FAILED $($file.Name): $($_.Exception.Message)  (close PowerPoint if the file is in use, then rerun)"
        $failed++
    }
}
Write-Host "done: $installed installed, $failed failed. Restart PowerPoint."
