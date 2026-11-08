param(
  [int]$GamePid = 0,                  # the last game of ours (relay game-pid.json); 0 when there is none
  [string]$Creation = '',
  [int]$TelnetPort = 47823
)
# ai-commander-arena season 2 pre-game health check (arena/harness/gamequeue.py Health). Read only: it stops and
# removes nothing. Prints one line "HEALTH {json}": game_alive (our last game: pid + creation time match),
# foreign_games (other FlashpointCampaigns processes: someone else's game, never touched), telnet_listeners (PIDs on
# the Telnet port), tokens (relay-*.token files left in ai-arena\in).
$ErrorActionPreference = 'Stop'
# game root: FPC_GAME_ROOT, else the job's working directory (jobs are started with the game folder as cwd)
$root = if ($env:FPC_GAME_ROOT) { $env:FPC_GAME_ROOT } else { (Get-Location).Path }
$in = Join-Path $root 'ai-arena\in'
$alive = $false
if ($GamePid -gt 0 -and $Creation) {
  $p = Get-CimInstance Win32_Process -Filter "ProcessId=$GamePid" -ErrorAction SilentlyContinue
  if ($p -and [math]::Abs(($p.CreationDate - [datetime]::Parse($Creation)).TotalSeconds) -le 1) { $alive = $true }
}
$games = @(Get-CimInstance Win32_Process -Filter "Name='FlashpointCampaigns.exe'" -ErrorAction SilentlyContinue | ForEach-Object { [int]$_.ProcessId })
$foreign = @($games | Where-Object { -not ($alive -and $_ -eq $GamePid) })
$listen = @(Get-NetTCPConnection -LocalPort $TelnetPort -State Listen -ErrorAction SilentlyContinue | ForEach-Object { [int]$_.OwningProcess } | Sort-Object -Unique)
$tokens = @(Get-ChildItem -LiteralPath $in -Filter 'relay-*.token' -File -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
'HEALTH ' + (@{ game_alive = $alive; foreign_games = $foreign; telnet_listeners = $listen; tokens = $tokens } | ConvertTo-Json -Compress)
