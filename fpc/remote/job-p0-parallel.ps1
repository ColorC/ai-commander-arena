param(
  [string]$Scenario = 'Chance Encounter',
  [int]$RunForMinutes = 10,
  [int]$MaxMinutes = 25
)
# ai-commander-arena P0.7: two FPC instances at once (computer vs computer, recorder only), different telnet ports
# and recorder output folders; the second starts in the next wall-clock minute (the game names its debug log and
# auto-saves by start minute). Checks both advance, then lists what each wrote. Stops only our own two processes
# (pid + creation time).
$ErrorActionPreference = 'Stop'
# game root: FPC_GAME_ROOT, else the job's working directory (jobs are started with the game folder as cwd)
$root = if ($env:FPC_GAME_ROOT) { $env:FPC_GAME_ROOT } else { (Get-Location).Path }
$exe = Join-Path $root 'FlashpointCampaigns.exe'
$arena = Join-Path $root 'ai-arena'
$env:FPC_ARENA_DIR = $arena + '\'   # recorder.lua reads ai-arena\in\run.lua from here (CreateProcess hands the environment on; a game started by Win32_Process.Create falls back to its working directory)
un.lua here (the game inherits the environment)
$runDir = Join-Path $arena ('out\par-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force (Join-Path $arena 'in'), $runDir | Out-Null
$jobLog = Join-Path $runDir 'job.log'
$t0 = Get-Date
function Log([string]$m) { $line = '{0:HH:mm:ss} {1}' -f (Get-Date), $m; Add-Content -Path $jobLog -Value $line -Encoding utf8; Write-Output $line }

$games = @()
foreach ($i in 1, 2) {
  $out = Join-Path $runDir "g$i"
  New-Item -ItemType Directory -Force $out | Out-Null
  # recorder.lua reads in\run.lua once at load: write it, start the game, wait for the load line
  "return { out_dir = [[$out\]], keyframe_seconds = 1800 }" | Out-File (Join-Path $arena 'in\run.lua') -Encoding ascii
  $port = 47822 + $i
  $cmd = '"{0}" -Scenario "{1}" -StyleOfPlay 2 -Quiet 1 -TimedMessages 1 -luafile "{2}" -telnet 127.0.0.1:{3} -config "arena.ini" -RunForMinutes {4}' -f $exe, $Scenario, (Join-Path $arena 'recorder.lua'), $port, $RunForMinutes
  $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]7 }
  $res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = $root; ProcessStartupInformation = $startup }
  if ($res.ReturnValue -ne 0) { Log "game $i create failed $($res.ReturnValue)"; break }
  $gp = Get-CimInstance Win32_Process -Filter "ProcessId=$($res.ProcessId)"
  $g = [pscustomobject]@{ i = $i; pid = [int]$res.ProcessId; created = $gp.CreationDate; out = $out; port = $port; started = Get-Date }
  $games += $g
  Log "game $i pid $($g.pid) port $port out $out"
  $recLog = Join-Path $out 'rec.log'
  $until = (Get-Date).AddSeconds(150)
  while ((Get-Date) -lt $until -and -not ((Test-Path $recLog) -and (Select-String -Path $recLog -Pattern 'recorder loaded' -SimpleMatch -Quiet))) { Start-Sleep -Seconds 2 }
  Log "game $i recorder loaded: $((Test-Path $recLog) -and (Select-String -Path $recLog -Pattern 'recorder loaded' -SimpleMatch -Quiet))"
  # the second game starts as soon as the wall-clock minute differs from the first one's start minute
  if ($i -eq 1) { while ((Get-Date).ToString('HHmm') -eq $g.started.ToString('HHmm')) { Start-Sleep -Milliseconds 500 }; Start-Sleep -Seconds 2 }
}

function Alive($g) { $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($g.pid)" -ErrorAction SilentlyContinue; return ($p -and $p.CreationDate -eq $g.created) }
function Status($g) { $f = Join-Path $g.out 'status.json'; if (Test-Path $f) { try { return Get-Content $f -Raw | ConvertFrom-Json } catch {} }; return $null }

$end = $t0.AddMinutes($MaxMinutes)
while ((Get-Date) -lt $end) {
  $line = foreach ($g in $games) { $s = Status $g; "g$($g.i) alive=$(Alive $g) sec=$($s.sec) ended=$($s.ended)" }
  Log ($line -join ' | ')
  if (-not ($games | Where-Object { (Alive $_) -and -not (Status $_).ended })) { break }
  Start-Sleep -Seconds 30
}
Start-Sleep -Seconds 20
foreach ($g in $games) {
  if (Alive $g) {
    $p = Get-Process -Id $g.pid
    [void]$p.CloseMainWindow()
    if (-not $p.WaitForExit(15000)) { Stop-Process -Id $g.pid -Force; Start-Sleep -Seconds 2 }
  }
  Log "game $($g.i) pid $($g.pid) alive after stop: $(Alive $g)"
}
# what the game wrote during this run, with sizes, to see whether the two instances overwrote each other
foreach ($d in @('Modules\FCCW\Test', 'Modules\FCCW\Saved')) {
  Get-ChildItem -Path (Join-Path $root $d) -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -ge $t0 } | ForEach-Object {
    Log "wrote $d\$($_.Name) $($_.Length) $($_.LastWriteTime.ToString('HH:mm:ss'))"
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $runDir (($d -replace '[\\]', '_') + '__' + $_.Name))
  }
}
Log 'job done'
