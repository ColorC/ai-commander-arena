param(
  # '|'-separated scenario names; empty = every folder under Modules\FCCW\Scenarios except the tutorials
  [string]$Only = '',
  [int]$LoadWaitSeconds = 180,
  # optional probe loaded after the recorder (file name under ai-arena\), its run.lua fields, and the file in the
  # scenario's output folder it writes; the game is stopped only once that file exists too
  [string]$Extra = '', [string]$ExtraFields = '', [string]$WaitFor = ''
)
# ai-commander-arena P1 step 1: the victory levels, VP locations, H hour and duration of a scenario exist only in the
# briefing the game writes into the 0 % auto-save when the scenario starts (not in the .scn). For each scenario:
# start it computer-vs-computer with recorder.lua (its load snapshot lists every unit with its side), wait for the
# load snapshot and the 0 % auto-save, copy both, then stop our own game process (pid + creation time). One game at
# a time, window minimized.
$ErrorActionPreference = 'Stop'
# game root: FPC_GAME_ROOT, else the job's working directory (jobs are started with the game folder as cwd)
$root = if ($env:FPC_GAME_ROOT) { $env:FPC_GAME_ROOT } else { (Get-Location).Path }
$exe = Join-Path $root 'FlashpointCampaigns.exe'
$arena = Join-Path $root 'ai-arena'
$env:FPC_ARENA_DIR = $arena + '\'   # recorder.lua reads ai-arena\in\run.lua from here (CreateProcess hands the environment on; a game started by Win32_Process.Create falls back to its working directory)
un.lua here (the game inherits the environment)
$runDir = Join-Path $arena ('out\p1-briefs-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force (Join-Path $arena 'in'), $runDir | Out-Null
$jobLog = Join-Path $runDir 'job.log'
function Log([string]$m) { $line = '{0:HH:mm:ss} {1}' -f (Get-Date), $m; Add-Content -Path $jobLog -Value $line -Encoding utf8; Write-Output $line }

$names = if ($Only) { $Only -split '\|' } else {
  Get-ChildItem (Join-Path $root 'Modules\FCCW\Scenarios') -Directory | Where-Object { $_.Name -notlike 'Tutorial*' } | ForEach-Object { $_.Name }
}
$savedDirs = Get-ChildItem (Join-Path $root 'Modules') -Directory | ForEach-Object { Join-Path $_.FullName 'Saved' } | Where-Object { Test-Path -LiteralPath $_ }
Log "scenarios: $($names.Count), auto-save folders: $($savedDirs -join '; ')"

function Alive($pid0, $created) { $p = Get-CimInstance Win32_Process -Filter "ProcessId=$pid0" -ErrorAction SilentlyContinue; return ($p -and $p.CreationDate -eq $created) }

foreach ($name in $names) {
  $safe = $name -replace '[^A-Za-z0-9]+', '_'
  $out = Join-Path $runDir $safe
  New-Item -ItemType Directory -Force $out | Out-Null
  $more = if ($Extra) { ", extra = [[$(Join-Path $arena $Extra)]], $ExtraFields" } else { '' }
  "return { out_dir = [[$out\]], keyframe_seconds = 999999$more }" | Out-File (Join-Path $arena 'in\run.lua') -Encoding ascii
  $t = Get-Date
  $cmd = '"{0}" -Scenario "{1}" -StyleOfPlay 2 -Quiet 1 -TimedMessages 1 -luafile "{2}" -config "arena.ini" -RunForMinutes 60' -f $exe, $name, (Join-Path $arena 'recorder.lua')
  $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]7 }
  $res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = $root; ProcessStartupInformation = $startup }
  if ($res.ReturnValue -ne 0) { Log "$name create failed $($res.ReturnValue)"; continue }
  $gpid = [int]$res.ProcessId
  $created = (Get-CimInstance Win32_Process -Filter "ProcessId=$gpid").CreationDate
  $recFile = Join-Path $out 'rec.jsonl'
  $save = $null; $snap = $false
  $until = $t.AddSeconds($LoadWaitSeconds)
  while ((Get-Date) -lt $until -and (Alive $gpid $created)) {
    if (-not $snap -and (Test-Path $recFile)) { $snap = [bool](Select-String -Path $recFile -Pattern '"k":"snap"' -SimpleMatch -Quiet) }
    if (-not $save) {
      $save = $savedDirs | ForEach-Object { Get-ChildItem -LiteralPath $_ -Filter '*.sav' -File } |
        Where-Object { $_.LastWriteTime -ge $t -and $_.Name -like '*@ 00_pct*' } | Select-Object -First 1
    }
    if ($snap -and $save -and (-not $WaitFor -or (Test-Path (Join-Path $out $WaitFor)))) { break }
    Start-Sleep -Seconds 2
  }
  $secs = [int]((Get-Date) - $t).TotalSeconds
  if ($save) { Start-Sleep -Seconds 2; Copy-Item -LiteralPath $save.FullName -Destination (Join-Path $out 'start.sav') }
  $alive = Alive $gpid $created
  Log "$name pid $gpid snap=$snap save=$([bool]$save) alive=$alive after ${secs}s"
  if ($alive) {
    Stop-Process -Id $gpid -Force
    $w = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $w -and (Alive $gpid $created)) { Start-Sleep -Milliseconds 500 }
    if (Alive $gpid $created) { Log "$name pid $gpid still alive after stop" }
  }
}
Log 'job done'
