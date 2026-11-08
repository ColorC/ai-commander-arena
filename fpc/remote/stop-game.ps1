param(
  [int]$GamePid,
  [string]$Creation
)
# Stop our own FPC process only: match pid + creation time, ask it to close, then force after 15 s.
$ErrorActionPreference = 'Stop'
$want = [datetime]::Parse($Creation)
$p = Get-CimInstance Win32_Process -Filter "ProcessId=$GamePid" -ErrorAction SilentlyContinue
if (-not $p) { Write-Output "pid $GamePid not running"; exit 0 }
if ([math]::Abs(($p.CreationDate - $want).TotalSeconds) -gt 1) { Write-Output "pid $GamePid creation $($p.CreationDate.ToString('o')) does not match $Creation; not touching it"; exit 4 }
$proc = Get-Process -Id $GamePid
$asked = $proc.CloseMainWindow()
Write-Output "CloseMainWindow sent: $asked"
if (-not $proc.WaitForExit(15000)) {
  Stop-Process -Id $GamePid -Force
  Start-Sleep -Seconds 2
  Write-Output 'forced stop'
}
$left = Get-CimInstance Win32_Process -Filter "ProcessId=$GamePid" -ErrorAction SilentlyContinue
Write-Output "alive after stop: $([bool]($left -and [math]::Abs(($left.CreationDate - $want).TotalSeconds) -le 1))"
