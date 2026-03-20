Param(
  [string]$ProjectPath,
  [string]$ScriptPath,
  [string]$UnityExe,
  [int]$TimeoutSeconds = 600,
  [string]$LogDir = ".\\temp\\unity_lint",
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Normalize-ToForwardSlash([string]$s) {
  if ($null -eq $s) { return "" }
  return ($s -replace '\\', '/')
}

function Normalize-AssetPath {
  Param(
    [string]$InputPath,
    [string]$ProjectPath
  )

  if ([string]::IsNullOrWhiteSpace($InputPath)) { return "" }

  $p = Normalize-ToForwardSlash $InputPath
  if ($p.StartsWith("Assets/", [System.StringComparison]::OrdinalIgnoreCase)) {
    return $p
  }

  $assetsDir = Normalize-ToForwardSlash (Join-Path $ProjectPath "Assets")
  if ([System.IO.Path]::IsPathRooted($InputPath) -and $p.StartsWith($assetsDir, [System.StringComparison]::OrdinalIgnoreCase)) {
    $rel = $p.Substring($assetsDir.Length).TrimStart('/')
    return "Assets/$rel"
  }

  # Last resort: if the input already contains "/Assets/" anywhere, extract from there.
  $idx = $p.IndexOf("/Assets/", [System.StringComparison]::OrdinalIgnoreCase)
  if ($idx -ge 0) {
    return $p.Substring($idx + 1)
  }

  return $p
}

function Extract-UnityCsDiagnostics {
  Param(
    [string]$LogFilePath,
    [string]$AssetPath,
    [string]$ScriptPath,
    [string]$FileNameOnly
  )

  if (!(Test-Path $LogFilePath)) {
    throw "Unity log file not found: $LogFilePath"
  }

  $lines = Get-Content -Path $LogFilePath

  # Typical Unity compiler format includes: "(10,12): error CSxxxx: ...", warnings: "warning CSxxxx: ..."
  $csDiagRegex = '(?i)\b(error|warning)\s+CS\d+\b'

  $assetPathNorm = Normalize-ToForwardSlash $AssetPath
  $scriptPathNorm = Normalize-ToForwardSlash $ScriptPath

  $matched = New-Object System.Collections.Generic.List[string]
  foreach ($line in $lines) {
    if ($line -notmatch $csDiagRegex) { continue }
    $hit =
      ($assetPathNorm -and ($line -like "*$assetPathNorm*")) -or
      ($scriptPathNorm -and ($line -like "*$scriptPathNorm*")) -or
      ($FileNameOnly -and ($line -like "*$FileNameOnly*"))

    if ($hit) {
      $matched.Add($line) | Out-Null
    }
  }

  return $matched
}

function Resolve-UnityExe {
  Param([string]$UnityExe)

  if (-not [string]::IsNullOrWhiteSpace($UnityExe) -and (Test-Path $UnityExe)) {
    return $UnityExe
  }

  $hubEditorDir = "C:\Program Files\Unity\Hub\Editor"
  if (!(Test-Path $hubEditorDir)) {
    # Also try 32-bit Program Files (some installs)
    $hubEditorDir = "C:\Program Files (x86)\Unity\Hub\Editor"
  }

  if (!(Test-Path $hubEditorDir)) {
    return ""
  }

  $candidates = Get-ChildItem -Path $hubEditorDir -Recurse -Filter "Unity.exe" -ErrorAction SilentlyContinue
  if ($null -eq $candidates -or $candidates.Count -eq 0) {
    return ""
  }

  # Prefer newest install by last write time.
  $best = ($candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1)
  return $best.FullName
}

if ($DryRun) {
  $sampleLog = @"
Some header
Assets/Chat/BadScript.cs(10,12): error CS1002: ; expected
Assets/Chat/BadScript.cs(20,5): warning CS0219: The variable 'x' is assigned but its value is never used
Assets/Other/Ok.cs(1,1): error CS0000: not related
"@

  $assetPath = "Assets/Chat/BadScript.cs"
  $scriptPath = "C:\Game\Assets\Chat\BadScript.cs"
  $fileNameOnly = "BadScript.cs"

  $tmp = Join-Path $env:TEMP "unity_lint_dryrun.log"
  Set-Content -Path $tmp -Value $sampleLog -Encoding UTF8

  $matched = Extract-UnityCsDiagnostics -LogFilePath $tmp -AssetPath $assetPath -ScriptPath $scriptPath -FileNameOnly $fileNameOnly
  Write-Host "Matched diagnostics:"
  foreach ($m in $matched) { Write-Host $m }
  exit 0
}

if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
  throw "-ProjectPath is required"
}
if ([string]::IsNullOrWhiteSpace($ScriptPath)) {
  throw "-ScriptPath is required (absolute path or Assets/.. path)"
}

$ProjectPathFull = [System.IO.Path]::GetFullPath($ProjectPath)
if (!(Test-Path $ProjectPathFull)) {
  throw "ProjectPath does not exist: $ProjectPathFull"
}

$unityExeResolved = Resolve-UnityExe -UnityExe $UnityExe
if ([string]::IsNullOrWhiteSpace($unityExeResolved)) {
  throw "UnityExe not provided and auto-discovery failed. Pass -UnityExe path to Unity.exe."
}

$assetPath = Normalize-AssetPath -InputPath $ScriptPath -ProjectPath $ProjectPathFull
$assetPathNorm = Normalize-ToForwardSlash $assetPath

if (-not $assetPathNorm.StartsWith("Assets/", [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "ScriptPath must resolve to an Assets/ path inside the Unity project. Resolved: $assetPathNorm"
}

$fileNameOnly = Split-Path -Leaf $assetPathNorm

$script:nowStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logDirFull = [System.IO.Path]::GetFullPath($LogDir)
if (!(Test-Path $logDirFull)) {
  New-Item -ItemType Directory -Path $logDirFull | Out-Null
}

$logFile = Join-Path $logDirFull ("unity_lint-$($script:nowStamp).log")

$executeMethod = "UnityLLM.Editor.Tools.UnityLlmScriptLintRunner.LintScript"

$args = @(
  "-batchmode",
  "-nographics",
  "-quit",
  "-projectPath", $ProjectPathFull,
  "-logFile", $logFile,
  "-executeMethod", $executeMethod,
  "-lintScriptPath", $ScriptPath,
  "-lintTimeoutSeconds", $TimeoutSeconds.ToString()
)

Write-Host "[lint-unity-script] Running Unity to lint:"
Write-Host "  UnityExe: $unityExeResolved"
Write-Host "  ProjectPath: $ProjectPathFull"
Write-Host "  ScriptPath: $ScriptPath"
Write-Host "  LogFile: $logFile"

$p = Start-Process -FilePath $unityExeResolved -ArgumentList $args -Wait -PassThru
if ($p.ExitCode -ne 0) {
  Write-Host "[lint-unity-script] Unity exited with code $($p.ExitCode) (still attempting to parse log)."
}

$matched = Extract-UnityCsDiagnostics -LogFilePath $logFile -AssetPath $assetPathNorm -ScriptPath $ScriptPath -FileNameOnly $fileNameOnly

Write-Host ""
Write-Host "[lint-unity-script] Diagnostics for ${assetPathNorm}:"

if ($matched.Count -eq 0) {
  Write-Host "(none)"
  exit 0
}

foreach ($m in $matched) {
  Write-Host $m
}

exit 0

