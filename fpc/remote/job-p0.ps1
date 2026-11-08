param(
  [string]$Scenario = 'Chance Encounter',
  [string]$Config = 'arena-h2h.ini',
  [int]$StyleOfPlay = 3,
  [int]$RunForMinutes = 40,
  [int]$MaxMinutes = 70,
  [string]$Telnet = '127.0.0.1:47823',
  [int]$StartWaitSeconds = 180,
  [int]$OrdersStallSeconds = 6,
  [int]$NoProgressMinutes = 6,
  # real-time gate schedule, game seconds (P0.2): sleep gate from the start until SleepTo; pause gate for
  # PauseSeconds from the first game second at or after PauseFrom that this job sees (turns resolve in bursts)
  [int]$SleepTo = 720,
  [int]$PauseFrom = 1500, [int]$PauseSeconds = 720,
  # P0.5: keep the end-of-game Scenario Information Center open (closing it after a RunForMinutes end crashed the
  # game) and shoot its result pages to <run>\end-*.bmp. With TimedMessages 0 a computer-vs-computer game waits at
  # its "Battle Over" dialog (shot too) and exits after it, without the information center. The information center's
  # tabs are also clicked by position (end-tab-*.bmp) and Victory Status is scrolled page by page (end-victory-<k>.bmp):
  # its Section C lists every VP location with its value and owner, unclaimed ones too (Eyes Ears and Teeth 11-03:
  # six locations, 13,500 VP, none of them the briefing's). The locations and their owners during the game are in the
  # scenario and save files (tools/fpc_objectives.py); the owners the game settles at the end are only here (ee-fr
  # 11-03: 9,500 at the 100 % save, 13,500 on Battle Over).
  # Then the other side's information center (end-enemy*.bmp), wheeled back to the top for its own level table.
  [switch]$EndScreens, [int]$Quiet = 1, [int]$TimedMessages = 1,
  # season 2 passive baseline (materials/18 section 3.1): the human side gives no orders at all; this job only presses
  # F9 when its Orders / setup phase has stalled (use with a config whose StyleOfPlay leaves one side to the computer)
  [switch]$Passive,
  # season 2 calibration: the probe's own test actions (a side-0 unit moved one hex at load, one order at 120 s) are
  # off; FreezeSide 0/1 in a computer-vs-computer game keeps that side's commander AI from changing any order
  [switch]$Calibration, [int]$FreezeSide = -1,
  # VP location probe (p0-probe.lua vp_probe): comma-separated hexes that a unit of VpProbeSide is put on at load
  [string]$VpProbe = '', [int]$VpProbeSide = 1,
  # output folder name under ai-arena\out (default p0-<time>), so a driver knows where to pull from
  [string]$RunName = '',
  # the game on a desktop of its own, out of sight of whoever uses the game machine (as relay.ps1); '' = this job's desktop
  [string]$Desktop = 'ai-arena'
)
# ai-commander-arena P0 probe run (materials/10 section 4, P0.1 P0.2 P0.3 P0.5 P0.8 P0.10; saves for P0.4).
# Hot-seat game (both sides human) with recorder.lua + p0-probe.lua. Each Orders phase: read the banner, find the
# side from the game's debug log ("Player N Orders Phase"), let the probe issue one order for that side, then F9.
# The gate file switches the probe's real-time gate between sleep / pause / off; in pause mode this job posts F9
# (through the probe) when wall time has caught up. At the end: window dumps of the end-of-game screens, then our
# own game process is stopped (pid + creation time) and logs / saves are collected.
$ErrorActionPreference = 'Stop'
# game root: FPC_GAME_ROOT, else the job's working directory (jobs are started with the game folder as cwd)
$root = if ($env:FPC_GAME_ROOT) { $env:FPC_GAME_ROOT } else { (Get-Location).Path }
$exe = Join-Path $root 'FlashpointCampaigns.exe'
$arena = Join-Path $root 'ai-arena'
$env:FPC_ARENA_DIR = $arena + '\'   # recorder.lua reads ai-arena\in\run.lua from here (CreateProcess hands the environment on; a game started by Win32_Process.Create falls back to its working directory)
un.lua here (the game inherits the environment)
$inbox = Join-Path $arena 'in'
$rec = Join-Path $arena ('out\' + $(if ($RunName) { $RunName } else { 'p0-' + (Get-Date -Format 'yyyyMMdd-HHmmss') }))
New-Item -ItemType Directory -Force $inbox, $rec | Out-Null
$jobLog = Join-Path $rec 'job.log'
$phaseLog = Join-Path $rec 'phases.txt'
$statusFile = Join-Path $rec 'status.json'
$gateFile = Join-Path $rec 'gate.txt'
$gateState = Join-Path $rec 'gate-state.json'
$t0 = Get-Date

function NowMs { return [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() }
function Log([string]$m) {
  $line = '{0:HH:mm:ss} {1} {2}' -f (Get-Date), (NowMs), $m
  Add-Content -Path $jobLog -Value $line -Encoding utf8
  Write-Output $line
}

$cal = if ($Calibration) { 'true' } else { 'false' }
$vp = if ($VpProbe) { "vp_probe = { $(($VpProbe -split ',' | ForEach-Object { [int]$_ }) -join ', ') }, vp_probe_side = $VpProbeSide, " } else { '' }
"return { out_dir = [[$rec\]], keyframe_seconds = 1800, extra = [[$arena\p0-probe.lua]], calibration = $cal, freeze_side = $FreezeSide, $vp}" | Out-File (Join-Path $inbox 'run.lua') -Encoding ascii
'off' | Out-File $gateFile -Encoding ascii

Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public static class Wn {
  [StructLayout(LayoutKind.Sequential)] struct RECT { public int L, T, R, B; }
  [StructLayout(LayoutKind.Sequential)] struct POINT { public int X, Y; }
  [DllImport("user32.dll", SetLastError = true)] static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] static extern int GetWindowLong(IntPtr h, int i);
  [DllImport("user32.dll")] static extern IntPtr ChildWindowFromPointEx(IntPtr h, POINT p, uint f);
  [DllImport("user32.dll")] static extern bool ScreenToClient(IntPtr h, ref POINT p);
  delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll", SetLastError = true)] static extern bool EnumDesktopWindows(IntPtr d, EnumProc f, IntPtr l);
  [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr CreateDesktop(string name, IntPtr dev, IntPtr dm, uint flags, uint access, IntPtr sa);
  [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr h);
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] struct STARTUPINFO {
    public int cb; public string lpReserved, lpDesktop, lpTitle;
    public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
    public short wShowWindow, cbReserved2; public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError; }
  [StructLayout(LayoutKind.Sequential)] struct PROCESS_INFORMATION { public IntPtr hProcess, hThread; public int dwProcessId, dwThreadId; }
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool CreateProcess(string app, StringBuilder cmd, IntPtr pa, IntPtr ta, bool inherit, uint flags, IntPtr env, string cwd, ref STARTUPINFO si, out PROCESS_INFORMATION pi);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
  // the game's own desktop, as in relay.ps1 (fpc/remote/job-desktop-probe.ps1, 11-02); zero: this job's desktop
  public static IntPtr Desk = IntPtr.Zero;
  static void Each(EnumProc f) { if (Desk != IntPtr.Zero) EnumDesktopWindows(Desk, f, IntPtr.Zero); else EnumWindows(f, IntPtr.Zero); }
  public static string MakeDesk(string name) {
    Desk = CreateDesktop(name, IntPtr.Zero, IntPtr.Zero, 0, 0x000F01FF, IntPtr.Zero);   // all DESKTOP_* rights
    return Desk == IntPtr.Zero ? "CreateDesktop failed " + Marshal.GetLastWin32Error() : "ok";
  }
  public static void CloseDesk() { if (Desk != IntPtr.Zero) CloseDesktop(Desk); }
  // the game started on that desktop, minimized and not activated, out of this job when the job lets it go
  // (Win32_Process.Create ignores the desktop it is asked for: probe 11-02 15:18); "pid<TAB>how" or "0<TAB>error"
  public static string Start(string cmd, string cwd, string desk) {
    var si = new STARTUPINFO(); si.cb = Marshal.SizeOf(si); si.lpDesktop = desk; si.dwFlags = 1; si.wShowWindow = 7;
    PROCESS_INFORMATION pi;
    string how = "out of the job";
    bool ok = CreateProcess(null, new StringBuilder(cmd), IntPtr.Zero, IntPtr.Zero, false, 0x01000000, IntPtr.Zero, cwd, ref si, out pi);
    if (!ok) {
      how = "in the job (breakaway refused " + Marshal.GetLastWin32Error() + ")";
      ok = CreateProcess(null, new StringBuilder(cmd), IntPtr.Zero, IntPtr.Zero, false, 0, IntPtr.Zero, cwd, ref si, out pi);
    }
    if (!ok) return "0\tCreateProcess failed " + Marshal.GetLastWin32Error();
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    return pi.dwProcessId + "\t" + how;
  }
  [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr p, EnumProc f, IntPtr l);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  static string Cls(IntPtr h) { var s = new StringBuilder(256); GetClassName(h, s, 256); return s.ToString(); }
  static string Txt(IntPtr h) { var s = new StringBuilder(4096); GetWindowText(h, s, 4096); return s.ToString(); }

  // relay.ps1's Top / Shot / Scroll / Under / Tap / Wheel: the information center's pages are tabs (a click by
  // caption leaves the Victory Status page: ee-pb 11-03 shot it three times) and Section C, every VP location with
  // its value and owner, is below the first screen
  static IntPtr Top(uint pid, string cls, bool visibleOnly) {
    IntPtr vis = IntPtr.Zero, any = IntPtr.Zero;
    Each((h, l) => {
      uint p; GetWindowThreadProcessId(h, out p);
      if (p != pid || Cls(h) != cls) return true;
      if (IsWindowVisible(h)) { vis = h; return false; }
      if (any == IntPtr.Zero) any = h;
      return true;
    });
    return vis != IntPtr.Zero || visibleOnly ? vis : any;
  }
  public static string Shot(uint pid, string cls, string path) {
    IntPtr top = Top(pid, cls, false);
    if (top == IntPtr.Zero) return "shot\tnil\tno visible " + cls;
    RECT r; GetWindowRect(top, out r);
    int w = r.R - r.L, hh = r.B - r.T;
    if (w <= 0 || hh <= 0) return "shot\tnil\tempty window";
    using (var bmp = new System.Drawing.Bitmap(w, hh, System.Drawing.Imaging.PixelFormat.Format24bppRgb))
    using (var g = System.Drawing.Graphics.FromImage(bmp)) {
      IntPtr dc = g.GetHdc(); bool ok = PrintWindow(top, dc, 2); g.ReleaseHdc(dc);
      if (!ok) return "shot\tnil\tPrintWindow failed";
      bmp.Save(path, System.Drawing.Imaging.ImageFormat.Bmp);
    }
    return "shot\t" + w + "\t" + hh;
  }
  public static string Scroll(uint pid, string cls) {
    IntPtr top = Top(pid, cls, true);
    if (top == IntPtr.Zero) return "scroll\t0\tno visible " + cls;
    var hit = new List<string>();
    EnumChildWindows(top, (c, l) => {
      if (IsWindowVisible(c) && (GetWindowLong(c, -16) & 0x00200000) != 0) { PostMessage(c, 0x0115, (IntPtr)3, IntPtr.Zero); hit.Add(Cls(c)); }
      return true;
    }, IntPtr.Zero);
    return "scroll\t" + hit.Count + "\t" + string.Join(",", hit);
  }
  static IntPtr Under(IntPtr top, int x, int y, out POINT p) {
    RECT r; GetWindowRect(top, out r);
    IntPtr h = top;
    while (true) {
      p.X = r.L + x; p.Y = r.T + y; ScreenToClient(h, ref p);
      IntPtr c = ChildWindowFromPointEx(h, p, 1);
      if (c == IntPtr.Zero || c == h) return h;
      h = c;
    }
  }
  public static string Tap(uint pid, string cls, int x, int y) {
    IntPtr top = Top(pid, cls, true);
    if (top == IntPtr.Zero) return "tap\tnil\tno visible " + cls;
    POINT p; IntPtr h = Under(top, x, y, out p);
    var lp = (IntPtr)((p.Y << 16) | (p.X & 0xFFFF));
    if (!PostMessage(h, 0x0201, (IntPtr)1, lp) || !PostMessage(h, 0x0202, IntPtr.Zero, lp)) return "tap\tnil\tPostMessage failed " + Marshal.GetLastWin32Error();
    return "tap\t" + Cls(h) + "\t" + p.X + "," + p.Y;
  }
  public static string Wheel(uint pid, string cls, int x, int y, int notches) {
    IntPtr top = Top(pid, cls, true);
    if (top == IntPtr.Zero) return "wheel\tnil\tno visible " + cls;
    POINT p; IntPtr h = Under(top, x, y, out p);
    RECT r; GetWindowRect(top, out r);
    var lp = (IntPtr)(((r.T + y) << 16) | ((r.L + x) & 0xFFFF));
    for (int i = 0; i < Math.Abs(notches); i++)   // negative: up
      if (!PostMessage(h, 0x020A, (IntPtr)((notches < 0 ? 120 : -120) << 16), lp)) return "wheel\tnil\tPostMessage failed " + Marshal.GetLastWin32Error();
    return "wheel\t" + Cls(h) + "\t" + notches;
  }
  // visible top-level windows of the process as "class<TAB>title<TAB>child texts joined by |"
  public static List<string> Visible(uint pid) {
    var o = new List<string>();
    Each((h, l) => {
      uint p; GetWindowThreadProcessId(h, out p);
      if (p != pid || !IsWindowVisible(h)) return true;
      var kids = new List<string>();
      EnumChildWindows(h, (c, l2) => { var t = Txt(c); if (t.Length > 0 && kids.Count < 40) kids.Add(t.Replace("\r", " ").Replace("\n", " ")); return true; }, IntPtr.Zero);
      o.Add(Cls(h) + "\t" + Txt(h) + "\t" + string.Join("|", kids));
      return true;
    });
    return o;
  }
  public static List<string> Dump(uint pid) {
    var o = new List<string>();
    Each((h, l) => {
      uint p; GetWindowThreadProcessId(h, out p);
      if (p != pid) return true;
      o.Add(string.Format("TOP {0:x} {1} visible={2} \"{3}\"", h.ToInt64(), Cls(h), IsWindowVisible(h), Txt(h)));
      int n = 0;
      EnumChildWindows(h, (c, l2) => {
        if (n++ > 300) return false;
        var t = Txt(c);
        if (t.Length > 0) o.Add(string.Format("    {0} visible={1} \"{2}\"", Cls(c), IsWindowVisible(c), t.Replace("\r", " ").Replace("\n", " | ")));
        return true;
      }, IntPtr.Zero);
      return true;
    });
    return o;
  }
}
'@

$hostName, $port = $Telnet.Split(':')
$port = [int]$port
$latin = [System.Text.Encoding]::GetEncoding(28591)

function Telnet([string[]]$send = @(), [int]$bannerMs = 1500, [int]$lineMs = 2000) {
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

function Read-Json([string]$path) {
  if (-not (Test-Path $path)) { return $null }
  try { return Get-Content $path -Raw | ConvertFrom-Json } catch { return $null }
}

# last 64 KB of the game's debug log for this run (opened shared: the game keeps writing it)
function Debug-Tail {
  $d = Join-Path $root 'Modules\FCCW\Test'
  $f = Get-ChildItem -Path $d -Filter '*DEBUG*' -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -ge $t0 } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $f) { return '' }
  try {
    $fs = [IO.File]::Open($f.FullName, 'Open', 'Read', 'ReadWrite')
    $len = [Math]::Min(65536, $fs.Length)
    [void]$fs.Seek(-$len, 'End')
    $buf = New-Object byte[] $len
    [void]$fs.Read($buf, 0, $len)
    $fs.Close()
    return [Text.Encoding]::UTF8.GetString($buf)
  } catch { return '' }
}

function Orders-Side {
  $ms = [regex]::Matches((Debug-Tail), 'Player (\d) Orders Phase')
  if ($ms.Count -eq 0) { return 0 }
  return [int]$ms[$ms.Count - 1].Groups[1].Value
}

$lua = Join-Path $arena 'recorder.lua'
$cmdline = '"{0}" -Scenario "{1}" -StyleOfPlay {2} -Quiet {7} -TimedMessages {8} -luafile "{3}" -telnet {4} -RunForMinutes {6}' -f $exe, $Scenario, $StyleOfPlay, $lua, $Telnet, $Config, $RunForMinutes, $Quiet, $TimedMessages
if ($Config) { $cmdline += ' -config "{0}"' -f $Config }   # -Config '': no config file (the command-line StyleOfPlay then counts)
Log "cmdline: $cmdline"
# on its own desktop when asked (as relay.ps1); on this job's desktop by Win32_Process.Create as before when that
# desktop cannot be made or the game not started there
$gamePid, $where = 0, 'this job''s desktop'
if ($Desktop) {
  $made = [Wn]::MakeDesk($Desktop)
  if ($made -eq 'ok') {
    $pidText, $how = ([Wn]::Start($cmdline, $root, "WinSta0\$Desktop")) -split "`t"
    $gamePid = [int]$pidText
    if ($gamePid) { $where = "desktop $Desktop, $how" } else { Log "game not started on desktop ${Desktop}: $how"; [Wn]::CloseDesk(); [Wn]::Desk = [IntPtr]::Zero }
  } else { Log "desktop ${Desktop}: $made" }
}
if (-not $gamePid) {
  $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]7 }
  $res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmdline; CurrentDirectory = $root; ProcessStartupInformation = $startup }
  if ($res.ReturnValue -ne 0) { Log "Win32_Process.Create failed: $($res.ReturnValue)"; exit 3 }
  $gamePid = [int]$res.ProcessId
}
$gp = Get-CimInstance Win32_Process -Filter "ProcessId=$gamePid"
$created = $gp.CreationDate
@{ pid = $gamePid; creation = $created.ToString('o'); session = $gp.SessionId; cmdline = $cmdline; desktop = $(if ([Wn]::Desk -ne [IntPtr]::Zero) { $Desktop } else { '' }) } |
  ConvertTo-Json | Out-File (Join-Path $rec 'game-pid.json') -Encoding utf8
Log "game pid $gamePid created $($created.ToString('o')) session $($gp.SessionId) on $where"

function Game-Alive {
  $p = Get-CimInstance Win32_Process -Filter "ProcessId=$gamePid" -ErrorAction SilentlyContinue
  return ($p -and $p.CreationDate -eq $created)
}
function Dump-Windows([string]$tag) {
  $lines = [Wn]::Dump([uint32]$gamePid)
  $lines | Out-File (Join-Path $rec "windows-$tag.txt") -Encoding utf8
  Log "windows-$tag.txt: $($lines.Count) lines"
}

# 1) wait for the recorder (and probe) to load
$recLog = Join-Path $rec 'rec.log'
$deadline = $t0.AddSeconds($StartWaitSeconds)
while ((Get-Date) -lt $deadline -and (Game-Alive)) {
  if ((Test-Path $recLog) -and (Select-String -Path $recLog -Pattern 'extra ' -SimpleMatch -Quiet)) { break }
  Start-Sleep -Seconds 3
}
$probeLine = if (Test-Path $recLog) { (Select-String -Path $recLog -Pattern 'extra ' -SimpleMatch | Select-Object -Last 1).Line } else { $null }
Log "probe load line: $probeLine"
if (-not $probeLine) {
  # facility failure: the game never loaded our Lua (11-01: another game held the Telnet port). Stop our own game
  # here; the job used to die on the missing rec.log and leave the game running
  Log "facility failure: recorder not loaded after $StartWaitSeconds s; stopping game pid $gamePid"
  if (Game-Alive) { Stop-Process -Id $gamePid -Force; Start-Sleep -Seconds 3 }
  Log "game alive now: $(Game-Alive)"
  [Wn]::CloseDesk()
  exit 6
}
# any terminating error (a trap covers its whole scope, so also errors before the game exists): stop our own game
# before the job ends, never leave it running
trap {
  Log "error at line $($_.InvocationInfo.ScriptLineNumber): $($_.Exception.Message); stopping game pid $gamePid"
  if ($gamePid -and (Get-Command Game-Alive -ErrorAction SilentlyContinue) -and (Game-Alive)) {
    Stop-Process -Id $gamePid -Force -ErrorAction SilentlyContinue
  }
  [Wn]::CloseDesk()
  break
}

# 2) drive the game
$end = $t0.AddMinutes($MaxMinutes)
# top-level windows that stay visible during play and are not dialogs
$panels = @('TacGlowForm', 'TfGameDescr', 'TfMiniMap', 'TApplication', 'TfSpotlight', 'TfMain', 'TfLeader', 'TfOpMap')
$gate = 'off'; $pauseStart = $null; $curSide = 0; $lastSec = -1; $lastProgress = Get-Date; $nextTelnet = Get-Date
$ordersKey = ''; $ordersSince = $null; $f9Count = 0; $f9SameKey = 0; $finished = $false; $finishedAt = $null; $dumped = @{}
$resumeLatency = @()
while ((Get-Date) -lt $end -and (Game-Alive)) {
  $st = Read-Json $statusFile
  $sec = if ($st -and $null -ne $st.sec) { [int]$st.sec } else { -1 }
  if ($sec -gt $lastSec) { $lastSec = $sec; $lastProgress = Get-Date }
  if ($st -and $st.ended) { $finished = $true }

  # gate schedule by game second
  $want = 'off'
  if ($sec -lt $SleepTo) { $want = 'sleep' }
  if ($sec -ge $PauseFrom -and $null -eq $pauseStart) { $pauseStart = $sec }
  if ($null -ne $pauseStart -and $sec -lt $pauseStart + $PauseSeconds) { $want = 'pause' }
  if ($want -ne $gate) {
    $want | Out-File $gateFile -Encoding ascii
    Log "gate $gate -> $want at game sec $sec"
    $gate = $want
  }

  # pause gate: resume once wall time has caught up (or the gate was lifted)
  $gs = Read-Json $gateState
  if ($gs -and $gs.paused) {
    $now = NowMs
    if ($gate -ne 'pause' -or $now -ge [int64]$gs.resume_at_ms) {
      $r = Telnet @('print("resume", p0_resume())') 300 800
      $resumeLatency += ($now - [int64]$gs.resume_at_ms)
      Log "resume at game sec $sec, late by $($now - [int64]$gs.resume_at_ms) ms: $((($r -split "`n") | Where-Object { $_ -match '^resume' }) -join ' ')"
    }
    Start-Sleep -Milliseconds 200
    continue
  }

  # game dialogs: hand-over "Pass control to <nation> player" (tells whose phase comes next), announcements,
  # "Secure Transmission" (who watches turn resolution). Any other visible window that is not a known panel is
  # logged and offered the same buttons.
  $dlgs = @([Wn]::Visible([uint32]$gamePid) | Where-Object { ($_ -split "`t")[0] -notin $panels })
  if ($dlgs.Count -gt 0) {
    $cls, $title, $kids = $dlgs[0] -split "`t", 3
    if ($cls -eq 'TfTOC_ScnInfo' -and $EndScreens) { Log "end screen '$title' [$kids]"; $finished = $true; break }
    if ($title -eq 'Battle Over' -and $EndScreens -and -not $dumped.ContainsKey('battle-over')) {
      # the last dialog of a computer-vs-computer game (it exits after Proceed): shoot it first
      $r = Telnet @(('print("rep", p0_shot("{0}", "end-battle-over"))' -f $cls)) 300 1500
      Log "end screen end-battle-over: $((($r -split "`n") | Where-Object { $_ -match '^rep' }) -join ' ')"
      Dump-Windows 'end-battle-over'; $dumped['battle-over'] = 1
    }
    if ($title -match 'Pass control to (.+?) player') {
      $curSide = if ($Matches[1] -match 'Soviet|East German|Polish|Czech|Warsaw') { 1 } else { 0 }
    }
    $send = @()
    if ($kids -match 'with existing fog of war settings') {
      $send += 'print("click", p0_click("{0}", "Both players will watch turn resolution with existing fog of war settings"))' -f $cls
    }
    # 'Higher Command Intervention' (TfSuddenDeath: a side below the catastrophic loss threshold) asks the human side
    # [Continue Fighting|End Battle Now]: the arena's rule is that sudden death ends the game (FM01 30;
    # 11-01 dn-pb1, the Czech AI side breaking, hung on it for 6 min until the job stopped the game)
    $btn = @('&Proceed', '&OK', 'OK', '&Yes', '&Close', 'End Battle Now') | Where-Object { ('|' + $kids + '|').Contains('|' + $_ + '|') } | Select-Object -First 1
    if ($btn) { $send += 'print("click", p0_click("{0}", "{1}"))' -f $cls, $btn }
    if ($cls -eq 'TfVCRReplay') { $send += 'print("click", p0_close("TfVCRReplay"))' } # turn replay: skip it
    if ($send.Count -gt 0) {
      $r = Telnet $send 300 1000
      Log "dialog $cls '$title' [$kids] -> side $curSide; $((($r -split "`n") | Where-Object { $_ -match '^click' }) -join ' ')"
    } else {
      $k = "$cls/$title"
      if (-not $dumped.ContainsKey($k)) { Log "unknown visible window $cls '$title' [$kids]"; Dump-Windows "unknown-$cls"; $dumped[$k] = 1 }
      $panels += $cls # not a dialog we can answer; stop looking at it
    }
    $nextTelnet = (Get-Date).AddSeconds(1)
    Start-Sleep -Milliseconds 500
    continue
  }

  # banner / Pregame (setup) and Orders phases (not while the sleep gate holds the game thread)
  if ((Get-Date) -ge $nextTelnet) {
    $text = Telnet
    $b = Parse-Banner $text
    $nextTelnet = (Get-Date).AddSeconds($(if ($gate -eq 'sleep') { 5 } else { 2 })) # the sleep gate blocks the game thread inside minute callbacks
    if ($b -and $b.phase -match 'Orders|Pregame|Setup' -and $StyleOfPlay -ne 2) { # computer vs computer: no orders
      $setup = $b.phase -notmatch 'Orders'
      $key = "$($b.phase)/$($b.elapsed)/$curSide"
      if ($key -ne $ordersKey) {
        $ordersKey = $key; $ordersSince = Get-Date
        Add-Content $phaseLog -Value ("==== {0:HH:mm:ss} {1} side={2} debug-log player={3}`n{4}" -f (Get-Date), $key, $curSide, (Orders-Side), $text) -Encoding utf8
        if (-not $dumped.ContainsKey("$setup/$curSide")) { Dump-Windows "phase-$(if ($setup) { 'setup' } else { 'orders' })-side$curSide"; $dumped["$setup/$curSide"] = 1 }
        if ($Passive) {
          Log "$($b.phase) elapsed=$($b.elapsed) side=$curSide debug-log player=$(Orders-Side): passive, no orders"
        } else {
          $fn = if ($setup) { 'p0_deploy' } else { 'p0_orders' }
          $r = Telnet @("print(""p0o"", $fn($curSide))") 300 2000
          Log "$($b.phase) elapsed=$($b.elapsed) side=$curSide ${fn}: $((($r -split "`n") | Where-Object { $_ -match '^p0o' }) -join ' ')"
        }
        $f9SameKey = 0
      } elseif (((Get-Date) - $ordersSince).TotalSeconds -ge $OrdersStallSeconds) {
        if ($f9SameKey -lt 4) {
          $r = Telnet @('print("f9", arena_f9())') 300 1500
          $f9Count++; $f9SameKey++; $ordersSince = Get-Date
          Log "F9 #$f9Count for $key ($f9SameKey): $((($r -split "`n") | Where-Object { $_ -match '^f9' }) -join ' ')"
          if ($f9SameKey -eq 3) { Dump-Windows "stuck-$($key -replace '/','-')" }
        }
      }
    }
    if ($b -and $b.total -gt 0 -and $b.elapsed -ge $b.total) { $finished = $true }
  }
  if ($finished) {
    # ending on time: the recorder and the banner report the end before the Battle Over dialog shows; keep handling
    # dialogs (up to 3 min). Computer vs computer: until Battle Over has been shot (the game exits after Proceed).
    # Otherwise until the information center opens (the TfTOC_ScnInfo break above); 11-01 af-pb3 (StyleOfPlay 1)
    # broke out here at once and shot nothing, Battle Over showed a few seconds later.
    if ($EndScreens -and -not ($StyleOfPlay -eq 2 -and $dumped.ContainsKey('battle-over'))) {
      if (-not $finishedAt) { $finishedAt = Get-Date; Log "scenario finished at game sec $lastSec; waiting for the end screens" }
      if (((Get-Date) - $finishedAt).TotalSeconds -lt 180) { Start-Sleep -Milliseconds 500; continue }
    }
    Log "scenario finished at game sec $lastSec"; break
  }
  if (((Get-Date) - $lastProgress).TotalMinutes -ge $NoProgressMinutes) { Log "no game-time progress for $NoProgressMinutes min; stopping"; Dump-Windows 'noprogress'; break }
  Start-Sleep -Milliseconds 500
}
'off' | Out-File $gateFile -Encoding ascii
if ($resumeLatency.Count -gt 0) { Log "pause-gate resumes: $($resumeLatency.Count), late ms: $($resumeLatency -join ',')" }

# 3) end-of-game screens (P0.5), then stop our game
if ($EndScreens -and $finished -and (Game-Alive)) {
  Dump-Windows 'end-toc'
  # each result page of the information center: click its button (none for the page it opens on), then shoot
  foreach ($page in @(@('', 'end-toc'), @('Victory Status && Conditions', 'end-victory'), @("Butcher's Bill", 'end-bill'), @('Final Report', 'end-final'))) {
    if ($page[0]) { [void](Telnet @(('print("rep", p0_click("TfTOC_ScnInfo", "{0}"))' -f $page[0])) 300 3000) }
    $r = Telnet @(('print("rep", p0_shot("TfTOC_ScnInfo", "{0}"))' -f $page[1])) 300 1500
    Log "end screen $($page[1]): $((($r -split "`n") | Where-Object { $_ -match '^rep' }) -join ' ')"
  }
  foreach ($page in @(@(382, 'tab-bill'), @(255, 'tab-final'), @(555, 'tab-victory'))) {
    Log "end screen tab $($page[1]): $([Wn]::Tap([uint32]$gamePid, 'TfTOC_ScnInfo', $page[0], 40))"; Start-Sleep -Milliseconds 3000
    $prev = Join-Path $rec "end-$($page[1]).bmp"
    Log "end screen end-$($page[1]): $([Wn]::Shot([uint32]$gamePid, 'TfTOC_ScnInfo', $prev))"
  }
  $sc = [Wn]::Scroll([uint32]$gamePid, 'TfTOC_ScnInfo'); Log "end screen scroll: $sc"
  $wheel = ($sc -split "`t")[1] -eq '0'
  foreach ($k in 2..40) {
    $f = Join-Path $rec "end-victory-$k.bmp"
    if ($k -gt 2 -and -not $wheel) { Log "end screen scroll: $([Wn]::Scroll([uint32]$gamePid, 'TfTOC_ScnInfo'))" }
    if ($wheel) { Log "end screen wheel: $([Wn]::Wheel([uint32]$gamePid, 'TfTOC_ScnInfo', 400, 360, 8))" }
    Start-Sleep -Milliseconds 1000
    [void][Wn]::Shot([uint32]$gamePid, 'TfTOC_ScnInfo', $f)
    if ((Get-FileHash $f).Hash -eq (Get-FileHash $prev).Hash) {
      if ($wheel) { Remove-Item $f; Log "end screen end-victory-$($k): did not move (end of page)"; break }
      $wheel = $true
      Log "end screen wheel: $([Wn]::Wheel([uint32]$gamePid, 'TfTOC_ScnInfo', 400, 360, 8))"; Start-Sleep -Milliseconds 1000
      [void][Wn]::Shot([uint32]$gamePid, 'TfTOC_ScnInfo', $f)
      if ((Get-FileHash $f).Hash -eq (Get-FileHash $prev).Hash) { Remove-Item $f; Log "end screen end-victory-$($k): did not move"; break }
    }
    Log "end screen end-victory-$($k): shot"; $prev = $f
  }
  # the other side's information center, for its own A.1 level table: the button under the page (a real button, so
  # a click by caption works, unlike the tabs); it opens on top, so Shot and Tap find it first
  $r = Telnet @('print("rep", p0_click("TfTOC_ScnInfo", "View Enemy Scenario Information Center"))') 300 3000
  Log "end screen enemy: $((($r -split "`n") | Where-Object { $_ -match '^rep' }) -join ' ')"
  Start-Sleep -Seconds 3
  Dump-Windows 'end-enemy'
  Log "end screen end-enemy: $([Wn]::Shot([uint32]$gamePid, 'TfTOC_ScnInfo', (Join-Path $rec 'end-enemy.bmp')))"
  foreach ($page in @(@(382, 'enemy-bill'), @(255, 'enemy-final'), @(555, 'enemy-victory'))) {
    Log "end screen tab $($page[1]): $([Wn]::Tap([uint32]$gamePid, 'TfTOC_ScnInfo', $page[0], 40))"; Start-Sleep -Milliseconds 3000
    Log "end screen end-$($page[1]): $([Wn]::Shot([uint32]$gamePid, 'TfTOC_ScnInfo', (Join-Path $rec "end-$($page[1]).bmp")))"
  }
  # its Victory Status opens where the first one was left (Section C): back to the top for its A.1 level table
  Log "end screen wheel up: $([Wn]::Wheel([uint32]$gamePid, 'TfTOC_ScnInfo', 400, 360, -120))"; Start-Sleep -Milliseconds 1500
  Log "end screen end-enemy-victory-top: $([Wn]::Shot([uint32]$gamePid, 'TfTOC_ScnInfo', (Join-Path $rec 'end-enemy-victory-top.bmp')))"
  Log "end screen wheel: $([Wn]::Wheel([uint32]$gamePid, 'TfTOC_ScnInfo', 400, 360, 8))"; Start-Sleep -Milliseconds 1000
  Log "end screen end-enemy-victory-top2: $([Wn]::Shot([uint32]$gamePid, 'TfTOC_ScnInfo', (Join-Path $rec 'end-enemy-victory-top2.bmp')))"
} elseif ($finished) {
  Start-Sleep -Seconds 15; if (Game-Alive) { Dump-Windows 'end-15s' }
  Start-Sleep -Seconds 30; if (Game-Alive) { Dump-Windows 'end-45s' }
}
if (Game-Alive) {
  $t = Telnet @('print("snap", arena_snapshot("job_end"))', 'print(arena_windows())')
  $t | Out-File (Join-Path $rec 'telnet-end.txt') -Encoding utf8
  $proc = Get-Process -Id $gamePid
  $asked = $proc.CloseMainWindow()
  if (-not $proc.WaitForExit(15000)) { Stop-Process -Id $gamePid -Force; Start-Sleep -Seconds 3; Log "CloseMainWindow=$asked did not end it; forced" }
  Log "stopped game pid $gamePid; alive now: $(Game-Alive)"
} else { Log 'game already exited' }
Log "F9 sent $f9Count times; last game sec $lastSec"

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
[Wn]::CloseDesk()
Log 'job done'
