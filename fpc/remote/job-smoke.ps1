param(
  [string]$Scenario = 'Chance Encounter',
  # -Scenario + -StyleOfPlay 2 make FPC launch straight into the scenario (without -StyleOfPlay it stops at
  # the Setup menu); -config arena.ini is then applied after the scenario loads and sets both sides to
  # Computer control. Verified 2026-10-30: Player[0].Control: Computer, Player[1].Control: Computer.
  [string]$Config = 'arena.ini',
  [int]$StyleOfPlay = 2,
  [int]$RunForMinutes = 0,       # >0: pass -RunForMinutes (end-of-game override from the command line)
  [int]$MaxMinutes = 45,
  [string]$Telnet = '127.0.0.1:47823',
  [int]$StartWaitSeconds = 180,
  [int]$PollSeconds = 15,
  [int]$OrdersStallSeconds = 40,
  [int]$NoProgressMinutes = 6
)
# ai-commander-arena smoke run: computer vs computer with the Lua recorder.
# With both sides on Computer the game advances turns by itself. When a side is Human (no -config), the
# game waits in the Orders phase: the job reads the telnet banner for the phase and, after a stall, asks the
# recorder to post F9 (Start Turn Resolution) to the game's own main window.
# Stops our own game process (pid + creation time) at the end, collects logs and auto-saves.
$ErrorActionPreference = 'Stop'
# game root: FPC_GAME_ROOT, else the job's working directory (jobs are started with the game folder as cwd)
$root = if ($env:FPC_GAME_ROOT) { $env:FPC_GAME_ROOT } else { (Get-Location).Path }
$exe = Join-Path $root 'FlashpointCampaigns.exe'
$arena = Join-Path $root 'ai-arena'
$env:FPC_ARENA_DIR = $arena + '\'   # recorder.lua reads ai-arena\in\run.lua from here (CreateProcess hands the environment on; a game started by Win32_Process.Create falls back to its working directory)
un.lua here (the game inherits the environment)
$inbox = Join-Path $arena 'in'
$rec = Join-Path $arena ('out\rec-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force $inbox, $rec | Out-Null
$jobLog = Join-Path $rec 'job.log'
$drvLog = Join-Path $rec 'driver.tsv'
$statusFile = Join-Path $rec 'status.json'
$t0 = Get-Date

function Log([string]$m) {
  $line = '{0:HH:mm:ss} {1}' -f (Get-Date), $m
  Add-Content -Path $jobLog -Value $line -Encoding utf8
  Write-Output $line
}

# recorder output folder for this run
"return { out_dir = [[$rec\]], keyframe_seconds = 1800 }" | Out-File (Join-Path $inbox 'run.lua') -Encoding ascii
"wall`telapsed`ttotal`tphase`trec_sec`trecords`taction" | Out-File $drvLog -Encoding utf8

$hostName, $port = $Telnet.Split(':')
$port = [int]$port
$latin = [System.Text.Encoding]::GetEncoding(28591)

# one telnet session: banner + optional lines; returns the whole transcript ('' if the game did not answer)
function Telnet([string[]]$send = @(), [int]$bannerMs = 2000, [int]$lineMs = 2000) {
  $sb = New-Object System.Text.StringBuilder
  try {
    $client = New-Object System.Net.Sockets.TcpClient
    $iar = $client.BeginConnect($hostName, $port, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne(3000)) { $client.Close(); return '' }
    $client.EndConnect($iar)
    $stream = $client.GetStream()
    $buf = New-Object byte[] 65536
    $drain = {
      param($ms)
      $until = (Get-Date).AddMilliseconds($ms)
      while ((Get-Date) -lt $until) {
        if ($stream.DataAvailable) { $n = $stream.Read($buf, 0, $buf.Length); [void]$sb.Append($latin.GetString($buf, 0, $n)) }
        else { Start-Sleep -Milliseconds 100 }
      }
    }
    & $drain $bannerMs
    foreach ($l in $send) {
      [void]$sb.Append("`n>>> $l`n")
      $b = [System.Text.Encoding]::ASCII.GetBytes($l + "`r`n")
      $stream.Write($b, 0, $b.Length)
      & $drain $lineMs
    }
    $client.Close()
  } catch { [void]$sb.Append("`nEXC: $($_.Exception.Message)") }
  return $sb.ToString()
}

function Parse-Banner([string]$text) {
  $m = [regex]::Match($text, 'Elapsed scenario time: (\d+):(\d+) out of (\d+):(\d+), ([^\r\n]*)')
  if (-not $m.Success) { return $null }
  return [pscustomobject]@{
    elapsed = [int]$m.Groups[1].Value * 60 + [int]$m.Groups[2].Value
    total   = [int]$m.Groups[3].Value * 60 + [int]$m.Groups[4].Value
    phase   = $m.Groups[5].Value.Trim()
  }
}

function Read-Status {
  if (-not (Test-Path $statusFile)) { return $null }
  try { return Get-Content $statusFile -Raw | ConvertFrom-Json } catch { return $null }
}

$lua = Join-Path $arena 'recorder.lua'
$cmdline = '"{0}" -Scenario "{1}" -StyleOfPlay {2} -Quiet 1 -TimedMessages 1 -luafile "{3}" -telnet {4}' -f $exe, $Scenario, $StyleOfPlay, $lua, $Telnet
if ($Config) { $cmdline += ' -config "{0}"' -f $Config }
if ($RunForMinutes -gt 0) { $cmdline += " -RunForMinutes $RunForMinutes" }
Log "cmdline: $cmdline"
$startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]7 }
$res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmdline; CurrentDirectory = $root; ProcessStartupInformation = $startup }
if ($res.ReturnValue -ne 0) { Log "Win32_Process.Create failed: $($res.ReturnValue)"; exit 3 }
$gamePid = [int]$res.ProcessId
$gp = Get-CimInstance Win32_Process -Filter "ProcessId=$gamePid"
$created = $gp.CreationDate
@{ pid = $gamePid; creation = $created.ToString('o'); session = $gp.SessionId; cmdline = $cmdline } | ConvertTo-Json | Out-File (Join-Path $rec 'game-pid.json') -Encoding utf8
Log "game pid $gamePid created $($created.ToString('o')) session $($gp.SessionId)"

function Game-Alive {
  $p = Get-CimInstance Win32_Process -Filter "ProcessId=$gamePid" -ErrorAction SilentlyContinue
  return ($p -and $p.CreationDate -eq $created)
}

# 1) wait for the recorder to load
$recLog = Join-Path $rec 'rec.log'
$deadline = $t0.AddSeconds($StartWaitSeconds)
while ((Get-Date) -lt $deadline -and (Game-Alive)) {
  if ((Test-Path $recLog) -and (Select-String -Path $recLog -Pattern 'recorder loaded' -SimpleMatch -Quiet)) { break }
  Start-Sleep -Seconds 3
}
$loaded = (Test-Path $recLog) -and (Select-String -Path $recLog -Pattern 'recorder loaded' -SimpleMatch -Quiet)
Log "recorder loaded: $loaded (after $([int]((Get-Date) - $t0).TotalSeconds) s)"
if ($loaded) {
  $t = Telnet @('print(arena_windows())', 'print(GameAPI.GetScenario().title)')
  $t | Out-File (Join-Path $rec 'telnet-start.txt') -Encoding utf8
}

# 2) drive turns until the scenario ends, progress stops, or the deadline
$end = $t0.AddMinutes($MaxMinutes)
$lastElapsed = -1; $lastProgress = Get-Date
$ordersSince = $null; $f9Count = 0; $f9NoEffect = 0; $lastF9Elapsed = -1
$finished = $false; $nextStat = Get-Date
while ((Get-Date) -lt $end -and (Game-Alive)) {
  $b = Parse-Banner (Telnet)
  $st = Read-Status
  $action = ''
  $recSec = if ($st) { $st.sec } else { '' }
  if ($b) {
    $progress = [math]::Max($b.elapsed, $(if ($st -and $st.sec) { [int]($st.sec / 60) } else { 0 }))
    if ($progress -gt $lastElapsed) { $lastElapsed = $progress; $lastProgress = Get-Date }
    if ($b.phase -match 'Orders') {
      if (-not $ordersSince) { $ordersSince = Get-Date }
      if (((Get-Date) - $ordersSince).TotalSeconds -ge $OrdersStallSeconds) {
        if ($lastF9Elapsed -eq $b.elapsed) { $f9NoEffect++ } else { $f9NoEffect = 0 }
        if ($f9NoEffect -lt 3) {
          $r = Telnet @('print("f9", arena_f9())') 1000 2500
          $f9Count++; $lastF9Elapsed = $b.elapsed; $ordersSince = Get-Date
          $action = 'F9 ' + (($r -split "`n" | Where-Object { $_ -match '^f9' }) -join ' ').Trim()
        } else { $action = 'F9 had no effect 3 times; waiting' }
      }
    } else { $ordersSince = $null }
    if ($b.elapsed -ge $b.total -and $b.total -gt 0) { $finished = $true }
  } else { $action = 'no banner' }
  if ($st -and $st.ended) { $finished = $true }
  ("{0:HH:mm:ss}`t{1}`t{2}`t{3}`t{4}`t{5}`t{6}" -f (Get-Date), $b.elapsed, $b.total, $b.phase, $recSec, $(if ($st) { $st.records } else { '' }), $action) | Add-Content $drvLog -Encoding utf8
  if ($action) { Log "elapsed=$($b.elapsed) phase='$($b.phase)' rec_sec=$recSec $action" }
  if ($finished) { Log 'scenario finished'; break }
  if (((Get-Date) - $lastProgress).TotalMinutes -ge $NoProgressMinutes) { Log "no game-time progress for $NoProgressMinutes min; stopping"; break }
  if ((Get-Date) -ge $nextStat) {
    $p = Get-Process -Id $gamePid -ErrorAction SilentlyContinue
    if ($p) { Log ('elapsed={0} phase="{1}" rec_sec={2} ws={3:N0} MB cpu={4:N0}s' -f $b.elapsed, $b.phase, $recSec, ($p.WorkingSet64 / 1MB), $p.CPU) }
    $nextStat = (Get-Date).AddMinutes(2)
  }
  Start-Sleep -Seconds $PollSeconds
}
if ($finished) { Start-Sleep -Seconds 30 } # let end-of-game saves and reports land

# 3) final snapshot through the recorder, then stop our game
if (Game-Alive) {
  $t = Telnet @('print("snap", arena_snapshot("job_end"))', 'print(arena_windows())')
  $t | Out-File (Join-Path $rec 'telnet-end.txt') -Encoding utf8
  $proc = Get-Process -Id $gamePid
  $asked = $proc.CloseMainWindow()
  if (-not $proc.WaitForExit(15000)) { Stop-Process -Id $gamePid -Force; Start-Sleep -Seconds 3; Log "CloseMainWindow=$asked did not end it; forced" }
  Log "stopped game pid $gamePid; alive now: $(Game-Alive)"
} else { Log 'game already exited' }
Log "F9 sent $f9Count times; last elapsed $lastElapsed min"

# 4) collect what the game wrote during this run (known folders only)
$col = Join-Path $rec 'collected'
New-Item -ItemType Directory -Force $col | Out-Null
foreach ($d in @('Modules\FCCW\Test', 'Modules\FCCW\Saved', 'Modules\FCCW\Logs', 'Modules\FCCW\Reports', 'Modules\FCCW')) {
  $full = Join-Path $root $d
  if (-not (Test-Path $full)) { continue }
  Get-ChildItem -Path $full -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -ge $t0 } | ForEach-Object {
    $dest = Join-Path $col (($d -replace '[\\]', '_') + '__' + $_.Name)
    Copy-Item -LiteralPath $_.FullName -Destination $dest
    Log "collected $($_.FullName) ($($_.Length) bytes)"
  }
}
Get-ChildItem -Path $root -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -ge $t0 } | ForEach-Object { Log "root file touched: $($_.Name) $($_.Length)" }
Log 'job done'
