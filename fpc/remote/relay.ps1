param(
  [string]$Scenario = 'Alamo at Soest',
  [string]$Config = 'arena-h2h.ini',
  [int]$StyleOfPlay = 3,
  [string]$Telnet = '127.0.0.1:47823',
  [string]$Referee = 'http://127.0.0.1:41010',   # the referee as this machine reaches it
  [string]$TokenFile = '',            # the referee's <run>\secrets\relay.token, pushed next to this machine's game
  [string]$RunName = '',
  [int]$RunForMinutes = 0,            # 0: play to the scenario's own end
  [int]$MaxMinutes = 600,
  [int]$TimedMessages = 1,
  [int]$SyncMs = 300,
  [int]$ReplayCloseSec = 0,            # close the turn-resolution replay window after it has been open this long
  [int]$PassDeferSec = 20,             # longest wait for the referee's release before answering a hand-over dialog
  [int]$EndShotSec = 180,              # after the game's own end: longest wait for Battle Over / information center
  [string]$Desktop = 'ai-arena'        # the game on a desktop of its own, out of sight of whoever uses the game machine; '' = this job's
)
# ai-commander-arena relay (arena/DESIGN.md sections 1 and 3): runs on the game machine next to one FPC game.
# Starts the game (minimized, not activated, on a desktop of its own: -Desktop) with recorder.lua + arena-bridge.lua,
# then every SyncMs posts to the referee: game clock and phase, the bridge's newest state.json (raw text, not parsed here), new debug-log lines
# (contacts, orders phases, LUA> input), visible dialogs, and the result of the last release. It does what the reply
# says: gate.txt, release batch (arena_release over telnet), F9, dialog clicks, stop. It never presses F9 or releases
# orders on its own. Generic game-flow dialogs (hand-over, announcements, turn replay) are answered here as job-p0.ps1
# does. At the end it shoots the end-of-game screens and stops its own game process (pid + creation time).
$ErrorActionPreference = 'Stop'
# game root: FPC_GAME_ROOT, else the job's working directory (jobs are started with the game folder as cwd)
$root = if ($env:FPC_GAME_ROOT) { $env:FPC_GAME_ROOT } else { (Get-Location).Path }
$exe = Join-Path $root 'FlashpointCampaigns.exe'
$arena = Join-Path $root 'ai-arena'
$env:FPC_ARENA_DIR = $arena + '\'   # recorder.lua reads ai-arena\in\run.lua from here (CreateProcess hands the environment on; a game started by Win32_Process.Create falls back to its working directory)
un.lua here (the game inherits the environment)
if (-not $RunName) { $RunName = 'match-' + (Get-Date -Format 'yyyyMMdd-HHmmss') }
$out = Join-Path $arena "out\$RunName"
New-Item -ItemType Directory -Force (Join-Path $arena 'in'), $out | Out-Null
$jobLog = Join-Path $out 'relay.log'
$t0 = Get-Date
$auth = @{ Authorization = 'Bearer ' + $(if ($TokenFile) { (Get-Content -LiteralPath $TokenFile -Raw).Trim() } else { '' }) }
function NowMs { return [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() }
function Log([string]$m) { $line = '{0:HH:mm:ss.fff} {1}' -f (Get-Date), $m; Add-Content -Path $jobLog -Value $line -Encoding utf8; Write-Output $line }

Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public static class Wn {
  delegate bool EnumProc(IntPtr h, IntPtr l);
  [StructLayout(LayoutKind.Sequential)] struct RECT { public int L, T, R, B; }
  [StructLayout(LayoutKind.Sequential)] struct POINT { public int X, Y; }
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
  // the game's own desktop (fpc/remote/job-desktop-probe.ps1, 11-02 18:08: nothing of the game on the user's desktop,
  // PrintWindow and PostMessage work from this thread as they do on one desktop); zero: this job's desktop
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
  [DllImport("user32.dll", SetLastError = true)] static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] static extern uint MapVirtualKey(uint c, uint t);
  [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] static extern int GetWindowLong(IntPtr h, int i);
  [DllImport("user32.dll")] static extern IntPtr ChildWindowFromPointEx(IntPtr h, POINT p, uint f);
  [DllImport("user32.dll")] static extern bool ScreenToClient(IntPtr h, ref POINT p);
  static string Cls(IntPtr h) { var s = new StringBuilder(256); GetClassName(h, s, 256); return s.ToString(); }
  static string Txt(IntPtr h) { var s = new StringBuilder(4096); GetWindowText(h, s, 4096); return s.ToString(); }
  static string Norm(string t) { return t.Replace("\r", " ").Replace("\n", " "); }
  // top-level window of the process with that class: a visible one first, else (unless visibleOnly) a hidden one
  // (the main form is hidden while the game runs minimized)
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
  // arena.dll's click / close / key / shot, posted from this process instead of a telnet Lua chunk: telnet runs its
  // Lua on another thread than the game's own callbacks (P3 rehearsal 4 10-30: a click chunk racing the minute
  // callback ended in an access violation), so nothing that lets the game thread go on may run as telnet Lua
  public static string Click(uint pid, string cls, string text) {
    IntPtr btn = IntPtr.Zero, top = IntPtr.Zero;
    Each((h, l) => {
      uint p; GetWindowThreadProcessId(h, out p);
      if (p != pid || !IsWindowVisible(h) || Cls(h) != cls) return true;
      top = h;
      EnumChildWindows(h, (c, l2) => { if (Norm(Txt(c)) == text) { btn = c; return false; } return true; }, IntPtr.Zero);
      return btn == IntPtr.Zero;
    });
    if (btn == IntPtr.Zero) return "click\tnil\t" + (top != IntPtr.Zero ? "no control \"" + text + "\"" : "no visible " + cls);
    if (!PostMessage(btn, 0x00F5, IntPtr.Zero, IntPtr.Zero)) return "click\tnil\tPostMessage failed " + Marshal.GetLastWin32Error();
    return "click\t" + btn.ToInt64() + "\t" + Txt(top);
  }
  public static string Close(uint pid, string cls) {
    IntPtr top = Top(pid, cls, true);
    if (top == IntPtr.Zero) return "click\tnil\tno visible " + cls;
    if (!PostMessage(top, 0x0010, IntPtr.Zero, IntPtr.Zero)) return "click\tnil\tPostMessage failed " + Marshal.GetLastWin32Error();
    return "click\t" + top.ToInt64() + "\t" + Txt(top);
  }
  public static string Key(uint pid, uint vk, string cls) {
    IntPtr top = Top(pid, cls, false);
    if (top == IntPtr.Zero) return "f9\tnil\tno window of class " + cls;
    long scan = MapVirtualKey(vk, 0), down = 1 | (scan << 16), up = down | (1L << 30) | (1L << 31);
    bool ok = PostMessage(top, 0x0100, (IntPtr)vk, (IntPtr)down) && PostMessage(top, 0x0101, (IntPtr)vk, (IntPtr)up);
    return ok ? "f9\t" + top.ToInt64() + "\tnil" : "f9\tnil\tPostMessage failed " + Marshal.GetLastWin32Error();
  }
  public static string Shot(uint pid, string cls, string path) {
    IntPtr top = Top(pid, cls, false);
    if (top == IntPtr.Zero) return "shot\tnil\tno visible " + cls;
    RECT r; GetWindowRect(top, out r);
    int w = r.R - r.L, hh = r.B - r.T;
    if (w <= 0 || hh <= 0) return "shot\tnil\tempty window";
    using (var bmp = new System.Drawing.Bitmap(w, hh, System.Drawing.Imaging.PixelFormat.Format24bppRgb))
    using (var g = System.Drawing.Graphics.FromImage(bmp)) {
      IntPtr dc = g.GetHdc(); bool ok = PrintWindow(top, dc, 2 /* PW_RENDERFULLCONTENT */); g.ReleaseHdc(dc);
      if (!ok) return "shot\tnil\tPrintWindow failed";
      bmp.Save(path, System.Drawing.Imaging.ImageFormat.Bmp);
    }
    return "shot\t" + w + "\t" + hh;
  }
  // one page down (WM_VSCROLL SB_PAGEDOWN) in every visible child of the window that has a vertical scroll bar: the
  // Victory Status page shows its VP location table (Section B) and how each owner was decided (Section C) only
  // below the first screen (start task 11-01: the owner of 3923 had to be worked out from the totals)
  public static string Scroll(uint pid, string cls) {
    IntPtr top = Top(pid, cls, true);
    if (top == IntPtr.Zero) return "scroll\t0\tno visible " + cls;
    var hit = new List<string>();
    EnumChildWindows(top, (c, l) => {
      if (IsWindowVisible(c) && (GetWindowLong(c, -16) & 0x00200000) != 0) {
        PostMessage(c, 0x0115, (IntPtr)3, IntPtr.Zero);
        hit.Add(Cls(c));
      }
      return true;
    }, IntPtr.Zero);
    return "scroll\t" + hit.Count + "\t" + string.Join(",", hit);
  }
  // the deepest visible child under (x, y) of the window as Shot captures it (its top left is 0, 0), and the point in
  // that child's client coordinates
  static IntPtr Under(IntPtr top, int x, int y, out POINT p) {
    RECT r; GetWindowRect(top, out r);
    IntPtr h = top;
    while (true) {
      p.X = r.L + x; p.Y = r.T + y; ScreenToClient(h, ref p);
      IntPtr c = ChildWindowFromPointEx(h, p, 1 /* CWP_SKIPINVISIBLE */);
      if (c == IntPtr.Zero || c == h) return h;
      h = c;
    }
  }
  // a left click at (x, y), posted to the child under it: the information center's pages are tabs of one tab control,
  // which Click cannot find by caption (start task 11-01: every end-bill / end-final shot showed the Victory Status page)
  public static string Tap(uint pid, string cls, int x, int y) {
    IntPtr top = Top(pid, cls, true);
    if (top == IntPtr.Zero) return "tap\tnil\tno visible " + cls;
    POINT p; IntPtr h = Under(top, x, y, out p);
    var lp = (IntPtr)((p.Y << 16) | (p.X & 0xFFFF));
    if (!PostMessage(h, 0x0201, (IntPtr)1, lp) || !PostMessage(h, 0x0202, IntPtr.Zero, lp)) return "tap\tnil\tPostMessage failed " + Marshal.GetLastWin32Error();
    return "tap\t" + Cls(h) + "\t" + p.X + "," + p.Y;
  }
  // mouse wheel down at (x, y), posted to the child under it as that many one-notch messages: for a page whose scroll
  // bar is not the window's own (WM_VSCROLL then does nothing); the information center's page moves the same for any
  // delta of one message (11-02), so the distance is the number of messages
  public static string Wheel(uint pid, string cls, int x, int y, int notches) {
    IntPtr top = Top(pid, cls, true);
    if (top == IntPtr.Zero) return "wheel\tnil\tno visible " + cls;
    POINT p; IntPtr h = Under(top, x, y, out p);
    RECT r; GetWindowRect(top, out r);
    var lp = (IntPtr)(((r.T + y) << 16) | ((r.L + x) & 0xFFFF));
    for (int i = 0; i < notches; i++)
      if (!PostMessage(h, 0x020A, (IntPtr)(-120 << 16), lp)) return "wheel\tnil\tPostMessage failed " + Marshal.GetLastWin32Error();
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
}
'@

# telnet: one short session per call (the game answers each line with the printed values) -------------------------
$tHost, $tPort = $Telnet.Split(':'); $tPort = [int]$tPort
$latin = [System.Text.Encoding]::GetEncoding(28591)
# $OnlyIf: the lines are sent only when it returns true for the banner text of this same session
function Telnet([string[]]$send = @(), [int]$bannerMs = 1200, [int]$lineMs = 1500, [scriptblock]$OnlyIf = $null) {
  $sb = New-Object System.Text.StringBuilder
  try {
    $client = New-Object System.Net.Sockets.TcpClient
    $iar = $client.BeginConnect($tHost, $tPort, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne(3000)) { $client.Close(); return '' }
    $client.EndConnect($iar)
    $stream = $client.GetStream(); $buf = New-Object byte[] 65536
    $drain = { param($ms) $until = (Get-Date).AddMilliseconds($ms); while ((Get-Date) -lt $until) { if ($stream.DataAvailable) { $n = $stream.Read($buf, 0, $buf.Length); [void]$sb.Append($latin.GetString($buf, 0, $n)) } else { Start-Sleep -Milliseconds 50 } } }
    & $drain $bannerMs
    if ($OnlyIf -and -not (& $OnlyIf $sb.ToString())) { $send = @(); [void]$sb.Append("`nSKIPPED") }
    foreach ($l in $send) { $b = [System.Text.Encoding]::ASCII.GetBytes($l + "`r`n"); $stream.Write($b, 0, $b.Length); [void]$sb.Append("`n>>> $l`n"); & $drain $lineMs }
    $client.Close()
  } catch { [void]$sb.Append("`nEXC: $($_.Exception.Message)") }
  return $sb.ToString()
}
function Banner([string]$text) {
  $m = [regex]::Match($text, 'Elapsed scenario time: (\d+):(\d+) out of (\d+):(\d+), ([^\r\n]*)')
  if (-not $m.Success) { return $null }
  return @{ elapsed = [int]$m.Groups[1].Value * 60 + [int]$m.Groups[2].Value; total = [int]$m.Groups[3].Value * 60 + [int]$m.Groups[4].Value; phase = $m.Groups[5].Value.Trim() }
}
function Reply([string]$text, [string]$tag) { return ((($text -split "`n") | Where-Object { $_ -match "^$tag" }) -join ' ') }
# JSON array built by hand: Windows PowerShell 5.1 unwraps one-element and empty arrays in ConvertTo-Json
function JArr($items, [int]$depth = 2) { return '[' + ((@($items) | Where-Object { $null -ne $_ } | ForEach-Object { ConvertTo-Json $_ -Depth $depth -Compress }) -join ',') + ']' }

# start the game ---------------------------------------------------------------------------------------------------
"return { out_dir = [[$out\]], keyframe_seconds = 1800, extra = [[$arena\arena-bridge.lua]] }" | Out-File (Join-Path $arena 'in\run.lua') -Encoding ascii
'off' | Out-File (Join-Path $out 'gate.txt') -Encoding ascii
$cmd = '"{0}" -Scenario "{1}" -StyleOfPlay {2} -Quiet 1 -TimedMessages {3} -luafile "{4}" -telnet {5}' -f $exe, $Scenario, $StyleOfPlay, $TimedMessages, (Join-Path $arena 'recorder.lua'), $Telnet
if ($Config) { $cmd += ' -config "{0}"' -f $Config }   # -Config '': no config file (the command-line StyleOfPlay then counts)
if ($RunForMinutes -gt 0) { $cmd += " -RunForMinutes $RunForMinutes" }
# on its own desktop when asked (its windows, dialogs and taskbar button never reach the user's desktop); on this job's
# desktop by Win32_Process.Create as before when that desktop cannot be made or the game not started there
$gamePid, $where = 0, 'this job''s desktop'
if ($Desktop) {
  $made = [Wn]::MakeDesk($Desktop)
  if ($made -eq 'ok') {
    $pidText, $how = ([Wn]::Start($cmd, $root, "WinSta0\$Desktop")) -split "`t"
    $gamePid = [int]$pidText
    if ($gamePid) { $where = "desktop $Desktop, $how" } else { Log "game not started on desktop ${Desktop}: $how"; [Wn]::CloseDesk(); [Wn]::Desk = [IntPtr]::Zero }
  } else { Log "desktop ${Desktop}: $made" }
}
if (-not $gamePid) {
  $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]7 }
  $res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = $root; ProcessStartupInformation = $startup }
  if ($res.ReturnValue -ne 0) { Log "game create failed $($res.ReturnValue)"; exit 3 }
  $gamePid = [int]$res.ProcessId
}
$created = (Get-CimInstance Win32_Process -Filter "ProcessId=$gamePid").CreationDate
@{ pid = $gamePid; creation = $created.ToString('o'); cmdline = $cmd; desktop = $(if ([Wn]::Desk -ne [IntPtr]::Zero) { $Desktop } else { '' }) } |
  ConvertTo-Json | Out-File (Join-Path $out 'game-pid.json') -Encoding utf8
Log "game pid $gamePid on $where; $cmd"
function Alive { $p = Get-CimInstance Win32_Process -Filter "ProcessId=$gamePid" -ErrorAction SilentlyContinue; return [bool]($p -and $p.CreationDate -eq $created) }
# any terminating error (a trap covers its whole scope): stop our own game (pid + creation time) before the job ends,
# never leave it running (11-01: a job that died left its game orphaned on the game machine). A killed job cannot do this; the
# season 2 director then stops the game by game-pid.json (tools/s2_director.py end_relay).
trap {
  Log "error at line $($_.InvocationInfo.ScriptLineNumber): $($_.Exception.Message); stopping game pid $gamePid"
  if ($gamePid -and (Get-Command Alive -ErrorAction SilentlyContinue) -and (Alive)) {
    Stop-Process -Id $gamePid -Force -ErrorAction SilentlyContinue
  }
  [Wn]::CloseDesk()
  break
}

# debug log: this game's file (named by scenario and start minute), read incrementally with shared access ------------
$debugFile = $null; $debugPos = [long]0; $debugTail = ''
$keep = [regex]'UpdateSpottedStepsAndReport\(\)|Orders Phase|LUA>|Player\[\d\]\.Control|Setup Phase|Deployment|ChangeSideNow'
# game clock of a debug-log line ("... - (07:28:00) ..."), seconds of the day; -1 when the line has none
function DayClock([string]$line) {
  $m = [regex]::Match($line, ' - \((\d\d):(\d\d):(\d\d)\)')
  if (-not $m.Success) { return -1 }
  return [int]$m.Groups[1].Value * 3600 + [int]$m.Groups[2].Value * 60 + [int]$m.Groups[3].Value
}
function New-DebugLines {
  if (-not $debugFile) {
    $stamps = @($t0.ToString('yyyyMMdd_HHmm'), $t0.AddMinutes(1).ToString('yyyyMMdd_HHmm'))
    $script:debugFile = Get-ChildItem -Path (Join-Path $root 'Modules\FCCW\Test') -Filter "*DEBUG ~$Scenario~*" -File -ErrorAction SilentlyContinue |
      Where-Object { $n = $_.Name; $stamps | Where-Object { $n.Contains($_) } } | Select-Object -First 1 -ExpandProperty FullName
    if (-not $debugFile) { return @() }
    Log "debug log $debugFile"
  }
  try {
    $fs = [IO.File]::Open($debugFile, 'Open', 'Read', 'ReadWrite')
    if ($fs.Length -le $debugPos) { $fs.Close(); return @() }
    [void]$fs.Seek($debugPos, 'Begin')
    $buf = New-Object byte[] ($fs.Length - $debugPos)
    $n = $fs.Read($buf, 0, $buf.Length); $fs.Close()
    $script:debugPos += $n
    $text = $debugTail + [Text.Encoding]::UTF8.GetString($buf, 0, $n)
    $parts = $text -split "`r?`n"
    $script:debugTail = $parts[-1]
    for ($i = $parts.Count - 2; $i -ge 0; $i--) { $c = DayClock $parts[$i]; if ($c -ge 0) { $script:debugClock = $c; break } }
    return @($parts[0..($parts.Count - 2)] | Where-Object { $keep.IsMatch($_) })
  } catch { return @() }
}

# sync loop ----------------------------------------------------------------------------------------------------------
$panels = @('TacGlowForm', 'TfGameDescr', 'TfMiniMap', 'TApplication', 'TfSpotlight', 'TfMain', 'TfLeader', 'TfOpMap', 'TacPngHintWindow', 'TForm')
$stateFile = Join-Path $out 'state.json'; $statusFile = Join-Path $out 'status.json'
$seq = 0; $lastStateWrite = [datetime]::MinValue; $gate = 'off'; $banner = $null; $nextBanner = Get-Date
$ordersSide = $null; $releaseResult = $null; $endShots = @(); $finished = $false; $fails = 0; $needBanner = $false; $lastPhase = 'Unknown'
$lastOrdersSide = $null; $lastMark = ''; $lastProgressAt = Get-Date; $endWaitUntil = $null
$ordersLineClock = -1; $debugClock = -1   # game clock of the last Orders phase line / of the newest debug-log line
$pending = New-Object System.Collections.Generic.List[string]   # debug lines not yet delivered to the referee
# While a player watches the turn-resolution replay the game does not write Lua input to its debug log, so releases
# and F9 then could not be audited: from a hand-over until the replay is over the relay reports hold=true and the
# referee waits (P2 smoke 10-30)
$holdUntil = [datetime]::MinValue; $replaySeen = $null; $resumeAfter = [datetime]::MinValue
$ordersDone = $true; $passDeferUntil = [datetime]::MinValue; $deferLogged = $false; $f9Pending = $false
$ackSec = -1; $ackWritten = -1
$end = $t0.AddMinutes($MaxMinutes)
# an error in one pass is logged and the next pass goes on; the relay ends (and stops its game below) only after 100
# failed passes in a row or 240 s without an answer from the referee (it ends the game itself after 180 s, RELAY_SILENT_SEC)
$loopErrors = 0; $lastSyncOk = Get-Date
while ((Get-Date) -lt $end) {
 try {
  $alive = Alive
  $seq++
  # game clock / end from the recorder's status.json
  $st = $null; try { if (Test-Path $statusFile) { $st = Get-Content $statusFile -Raw | ConvertFrom-Json } } catch {}
  $lines = @(New-DebugLines)
  $mark = "$(if ($st) { $st.sec })/$debugClock"; if ($mark -ne $lastMark) { $lastMark = $mark; $lastProgressAt = Get-Date }
  foreach ($l in $lines) { $pending.Add($l) }
  foreach ($l in $lines) {
    if ($l -match 'Player (\d) Orders Phase') {
      $ordersSide = [int]$Matches[1] - 1; $lastOrdersSide = $ordersSide; $needBanner = $true; $nextBanner = Get-Date; $ordersLineClock = DayClock $l
      $ordersDone = $false; $passDeferUntil = (Get-Date).AddSeconds($PassDeferSec); $deferLogged = $false; $f9Pending = $false
    }
  }

  # dialogs: answered here when they are only game flow; listed to the referee in any case
  $dialogs = @()
  $passVisible = $false
  if ($alive) {
    $vcrNow = $false
    foreach ($d in @([Wn]::Visible([uint32]$gamePid) | Where-Object { ($_ -split "`t")[0] -notin $panels })) {
      $cls, $title, $kids = $d -split "`t", 3
      $dialogs += @{ cls = $cls; title = $title; texts = @($kids -split '\|') }
      if ($title -eq 'Battle Over' -or $cls -eq 'TfTOC_ScnInfo') {
        if ($endShots -notcontains $cls) {
          $name = if ($cls -eq 'TfTOC_ScnInfo') { 'end-toc' } else { 'end-battle-over' }
          Start-Sleep -Milliseconds 300
          Log "end screen $cls '$title': $([Wn]::Shot([uint32]$gamePid, $cls, (Join-Path $out "$name.bmp")))"
          $endShots += $cls; $finished = $true
          if ($cls -eq 'TfTOC_ScnInfo') {
            # the page tabs along its top, at x of the 800 x 729 window (end-victory.png of 11-01): Victory Status &
            # Conditions 555, Butcher's Bill 382, Final Report 255
            foreach ($page in @(@(555, 'end-victory'), @(382, 'end-bill'), @(255, 'end-final'))) {
              Log "end screen tab $($page[1]): $([Wn]::Tap([uint32]$gamePid, 'TfTOC_ScnInfo', $page[0], 40))"; Start-Sleep -Milliseconds 3000
              $prev = Join-Path $out "$($page[1]).bmp"
              Log "end screen $($page[1]): $([Wn]::Shot([uint32]$gamePid, 'TfTOC_ScnInfo', $prev))"
              if ($page[1] -eq 'end-victory') {
                # the page is a TPaintPanel: WM_VSCROLL does nothing (11-01 baseline-A), and each wheel message moves it
                # about 50 px whatever its delta (11-02 ex-p0-1: 10 steps of one 30-notch message went from the top to
                # the first of six objectives of Section C); 8 messages a step is about two thirds of the 600 px page,
                # so consecutive shots overlap. Section C lists the units near each objective: up to 30 steps
                $sc = [Wn]::Scroll([uint32]$gamePid, 'TfTOC_ScnInfo'); Log "end screen scroll: $sc"
                $wheel = ($sc -split "`t")[1] -eq '0'
                foreach ($k in 2..31) {
                  $f = Join-Path $out "end-victory-$k.bmp"
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
              }
            }
          }
        }
        if ($title -ne 'Battle Over') { continue } # the information center: never press its Close (crashes the game)
      }
      # the hand-over dialog also comes before the other player watches the replay: whose Orders phase it is comes
      # from the debug log only ("Player N Orders Phase"). After an Orders phase line it stays open until the referee
      # has released that phase (orders_done, at most PassDeferSec): some Orders phases of the two-player game go on
      # by themselves once it is answered (replay, then resolution), so this is the only moment to release them
      if ($title -match 'Pass control to') {
        $passVisible = $true
        if (-not $ordersDone -and (Get-Date) -lt $passDeferUntil) {
          if (-not $deferLogged) { Log "hand-over '$title' kept open until the referee has released this Orders phase"; $deferLogged = $true }
          continue
        }
        $holdUntil = @($holdUntil, (Get-Date).AddSeconds(8)) | Sort-Object | Select-Object -Last 1
      }
      if ($title -match 'Resolution Replay Starting' -or $cls -eq 'TfVCRReplay') { $holdUntil = (Get-Date).AddSeconds(30) }
      # the briefback dialog starts the resolution: no F9 for a few seconds after it (see the F9 double read below)
      if ($cls -eq 'TfH2HBriefback') { $holdUntil = @($holdUntil, (Get-Date).AddSeconds(3)) | Sort-Object | Select-Object -Last 1 }
      # after the replay the game either resolves on by itself or comes back to the Orders phase and waits for F9
      # (P3 rehearsal 10-30: stuck 75 min at 08:45 reported as Executing): read the banner as after an Orders line
      if ($title -match 'Resolution Replay Concluded') { $holdUntil = (Get-Date).AddSeconds(1.5); $needBanner = $true; $nextBanner = (Get-Date).AddSeconds(2) }
      # answered by window messages from here, never by a telnet Lua chunk (see [Wn]::Click)
      $send = @()
      if ($kids -match 'with existing fog of war settings') { $send += ,@($cls, 'Both players will watch turn resolution with existing fog of war settings') }
      # 'Higher Command Intervention' (TfSuddenDeath: a side below the catastrophic loss threshold) asks the human side
      # [Continue Fighting|End Battle Now]: the arena's rule is that sudden death ends the game (FM01 30;
      # 11-01 dn-pb1, the Czech AI side breaking, hung on it for 6 min until the job stopped the game)
      $btn = @('&Proceed', '&OK', 'OK', '&Yes', '&Close', 'End Battle Now') | Where-Object { ('|' + $kids + '|').Contains('|' + $_ + '|') } | Select-Object -First 1
      if ($btn) { $send += ,@($cls, $btn) }
      if ($cls -eq 'TfVCRReplay') {
        $vcrNow = $true; if (-not $replaySeen) { $replaySeen = Get-Date }
        if (((Get-Date) - $replaySeen).TotalSeconds -ge $ReplayCloseSec) { $send += ,@('TfVCRReplay', $null) }
      }
      if ($send.Count -gt 0) {
        $res = foreach ($s in $send) {
          if ($null -eq $s[1]) { [Wn]::Close([uint32]$gamePid, $s[0]) } else { [Wn]::Click([uint32]$gamePid, $s[0], $s[1]) }
          Start-Sleep -Milliseconds 400   # as the telnet session did: the game handles one click before the next
        }
        Log "dialog $cls '$title' -> $($res -join ' ')"
      }
    }
    if (-not $vcrNow) { $replaySeen = $null }
  }

  # phase from the telnet banner. While the bridge holds the game thread inside a minute callback (lockstep wait or
  # real-time gate) telnet does not answer, so during execution the banner is read only after the debug log announced
  # an Orders phase (the game thread is then outside the minute callbacks). Not every "Player N Orders Phase" line
  # waits for F9 (in the two-player game some pass on by themselves), and the banner read right after the line can
  # still show the previous phase (smoke 10-30: stuck as Executing in a real Orders phase): after the line the banner
  # is read again every 2 s until it shows Orders or the game clock moves on
  # and a watchdog: with the gate off and no hold, a game clock that has not moved for 15 s means the game waits for
  # something (telnet answers then), whatever announced it or not
  $stalled = $gate -eq 'off' -and (Get-Date) -ge $holdUntil -and ((Get-Date) - $lastProgressAt).TotalSeconds -ge 15
  $wantBanner = $needBanner -or $stalled -or $lastPhase -in @('Orders', 'Setup', 'Unknown')
  if ($alive -and $wantBanner -and (Get-Date) -ge $nextBanner) {
    $b = Banner (Telnet @() 800 0); if ($b) { $banner = $b }; $nextBanner = (Get-Date).AddSeconds(2)
    # a paused resolution (an F9 that came during it, or a pause of the game's own) waits for F9 to go on: nobody
    # else will press it, so the relay does, at most every 10 s and only with no dialog open
    if ($b -and $b.phase -match 'Pause' -and $dialogs.Count -eq 0 -and (Get-Date) -ge $resumeAfter) {
      Log "F9 (resume: banner '$($b.phase)'): $([Wn]::Key([uint32]$gamePid, 0x78, 'TfMain'))"
      $resumeAfter = (Get-Date).AddSeconds(10)
    }
    $movedOn = ($debugClock - $ordersLineClock + 86400) % 86400
    if ($needBanner -and (($b -and $b.phase -match 'Orders') -or $ordersLineClock -lt 0 -or ($movedOn -gt 0 -and $movedOn -lt 43200))) { $needBanner = $false }
  }
  $phase = if (-not $alive) { 'Ended' } elseif ($st -and $st.ended) { 'Ended' } elseif ($banner) {
    if ($banner.phase -match 'Orders') { 'Orders' } elseif ($banner.phase -match 'Setup|Pregame|Deploy') { 'Setup' } else { 'Executing' } } else { $lastPhase }
  if ($phase -eq 'Executing' -and -not $needBanner) { $ordersSide = $null }
  if ($phase -eq 'Orders' -and $null -eq $ordersSide) { $ordersSide = $lastOrdersSide }   # back from the replay
  if ($phase -ne $lastPhase) { Log "phase $lastPhase -> $phase (orders side $ordersSide, game sec $($st.sec))" }
  $lastPhase = $phase

  # request body: the bridge's state.json is passed through as raw JSON text
  $stateText = 'null'
  # the bridge replaces state.json in one step: between Test-Path and Get-Item it can be gone for a moment (P4 training
  # p1-7 10-31: that error ended the relay and left its game running), so the whole read is tried
  try { if (Test-Path $stateFile) { $w = (Get-Item $stateFile).LastWriteTimeUtc; if ($w -ne $lastStateWrite) { $stateText = [IO.File]::ReadAllText($stateFile).Trim(); $lastStateWrite = $w } } } catch { $stateText = 'null' }
  if (-not $stateText) { $stateText = 'null' }
  $game = @{ sec = $(if ($st) { $st.sec } else { $null }); phase = $phase; orders_side = $ordersSide; alive = $alive; hold = ((Get-Date) -lt $holdUntil)
             elapsed = $(if ($banner) { $banner.elapsed } else { $null }); total = $(if ($banner) { $banner.total } else { $null }) }
  $body = '{"seq":' + $seq + ',"wall_ms":' + (NowMs) + ',"run":' + (ConvertTo-Json $RunName -Compress) + ',"game":' + (ConvertTo-Json $game -Compress) +
          ',"state":' + $stateText + ',"lines":' + (JArr $pending) + ',"dialogs":' + (JArr $dialogs 4) +
          ',"release_result":' + $(if ($releaseResult) { $releaseResult } else { 'null' }) + ',"end_shots":' + (JArr $endShots) + '}'
  try {
    $r = Invoke-RestMethod -Uri "$Referee/relay/sync" -Method Post -Headers $auth -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 10
    $fails = 0; $releaseResult = $null; $pending.Clear(); $lastSyncOk = Get-Date; $loopErrors = 0
  } catch {
    $fails++; if ($fails -le 3 -or $fails % 50 -eq 0) { Log "sync failed ($fails): $($_.Exception.Message)" }
    if ($stateText -ne 'null') { $lastStateWrite = [datetime]::MinValue } # resend the state next time
    if (((Get-Date) - $lastSyncOk).TotalSeconds -ge 240) { Log "no answer from the referee for 240 s: stop"; break }
    Start-Sleep -Milliseconds $SyncMs; continue
  }

  # do what the referee says
  # gate.txt "<mode> <ack sec>": the gate, and the newest game minute the referee has processed (bridge lockstep)
  if ($stateText -ne 'null') { $m = [regex]::Match($stateText, '"sec":(\d+)'); if ($m.Success) { $ackSec = [int]$m.Groups[1].Value } }
  $newGate = if ($r.gate) { [string]$r.gate } else { $gate }
  if ($newGate -ne $gate -or $ackSec -ne $ackWritten) {
    if ($newGate -ne $gate) { Log "gate $gate -> $newGate at game sec $($game.sec)" }
    $gateLine = if ($ackSec -ge 0) { "$newGate $ackSec" } else { $newGate }
    $gt = Join-Path $out 'gate.txt'; $gtmp = Join-Path $out 'gate.tmp'
    try {   # replaced in one step: the bridge must never read a missing or half-written gate file
      [IO.File]::WriteAllText($gtmp, $gateLine)
      # [NullString]::Value: PowerShell passes $null to a string parameter as "" (an invalid backup path, smoke 10-30)
      if (Test-Path $gt) { [IO.File]::Replace($gtmp, $gt, [NullString]::Value) } else { [IO.File]::Move($gtmp, $gt) }
      $gate = $newGate; $ackWritten = $ackSec
    } catch { Log "gate write failed (retry next sync): $($_.Exception.Message)" }
  }
  if ($r.release -and $alive) {
    $path = Join-Path $arena "in\release-$($r.release.batch_id).lua"
    [IO.File]::WriteAllText($path, [string]$r.release.lua, (New-Object System.Text.UTF8Encoding $false))
    # the one telnet Lua chunk left: it runs while the game thread waits in the Orders phase (no Lua of the game's
    # own then), and nothing that lets the game go on (F9, a click) is sent before its result file is there
    $t = Telnet @(('print("rel", arena_release([[{0}]]))' -f $path)) 300 3000
    $resFile = Join-Path $out "release-$($r.release.batch_id).json"
    $relUntil = (Get-Date).AddSeconds(20)
    while (-not (Test-Path $resFile) -and (Get-Date) -lt $relUntil -and -not $t.Contains('EXC:')) { Start-Sleep -Milliseconds 200 }
    Start-Sleep -Milliseconds 300
    $releaseResult = if (Test-Path $resFile) { [IO.File]::ReadAllText($resFile).Trim() } else { '{"batch_id":' + (ConvertTo-Json ([string]$r.release.batch_id)) + ',"error":' + (ConvertTo-Json (Reply $t 'rel')) + '}' }
    Log "release $($r.release.batch_id) side $($r.release.side): $(Reply $t 'rel')"
  }
  foreach ($c in @($r.click)) { if ($c -and $alive) { Log "referee click $($c.cls) '$($c.text)': $([Wn]::Click([uint32]$gamePid, [string]$c.cls, [string]$c.text))"; Start-Sleep -Milliseconds 400 } }
  # F9 waits until no hand-over dialog is open and no hold is on, then goes only while the telnet banner (no Lua)
  # read just before shows the game waiting (Orders or setup): in the two-player game some Orders phases go on by
  # themselves after the replay, and F9 during the resolution would pause it. The key is posted from here.
  if ($r.orders_done) { $ordersDone = $true }
  if ($r.f9) { $f9Pending = $true }
  if ($f9Pending -and $alive -and -not $passVisible -and (Get-Date) -ge $holdUntil) {
    $f9Pending = $false
    # two reads a second apart, both waiting at the same game time: right after the briefback dialog that starts the
    # resolution the banner can still say Orders, and F9 then pauses the resolution (P3 rehearsal 5 10-30, 00:29)
    $b = Banner (Telnet @() 800 0)
    if ($b -and $b.phase -match 'Orders|Setup|Pregame|Deploy') {
      Start-Sleep -Milliseconds 1000
      $b2 = Banner (Telnet @() 800 0)
      if (-not ($b2 -and $b2.phase -eq $b.phase -and $b2.elapsed -eq $b.elapsed)) { $b = $b2; if ($b) { $b = @{ elapsed = $b.elapsed; total = $b.total; phase = "$($b.phase) (changed)" } } }
    }
    if (-not ($b -and $b.phase -match 'Orders|Setup|Pregame|Deploy') -or $b.phase -match 'changed') {
      if ($b) { $banner = $b }
      Log "F9 ($phase side $ordersSide) skipped: banner '$(if ($b) { $b.phase } else { 'none' })'"
    } else {
      Log "F9 ($phase side $ordersSide): $([Wn]::Key([uint32]$gamePid, 0x78, 'TfMain'))"
      $banner = $null; $lastPhase = 'Executing'; $ordersSide = $null; $lastOrdersSide = $null; $nextBanner = (Get-Date).AddSeconds(3)
    }
  }
  if ($r.stop) {
    # a game that ended by itself shows Battle Over and the information center a little later: stay (the dialog pass
    # above answers and shoots them) until the information center is shot or EndShotSec is over (P3 rehearsal 3: the
    # game was stopped at once and the official VP never read)
    if ($alive -and $phase -eq 'Ended' -and $endShots -notcontains 'TfTOC_ScnInfo') {
      if (-not $endWaitUntil) { $endWaitUntil = (Get-Date).AddSeconds($EndShotSec); Log "referee: stop; waiting up to $EndShotSec s for the end screens" }
      if ((Get-Date) -lt $endWaitUntil) { Start-Sleep -Milliseconds $SyncMs; continue }
      Log "end screens: $($endShots -join ', ') after $EndShotSec s"
    }
    Log 'referee: stop'; break
  }
  if (-not $alive) { Log 'game process gone'; break }
  Start-Sleep -Milliseconds $SyncMs
 } catch {
  $loopErrors++
  if ($loopErrors -le 5 -or $loopErrors % 20 -eq 0) { Log "pass error ($loopErrors in a row) line $($_.InvocationInfo.ScriptLineNumber): $($_.Exception.Message)" }
  if ($loopErrors -ge 100) { Log 'too many errors in a row: stop'; break }
  Start-Sleep -Milliseconds $SyncMs
 }
}

if (Alive) {
  $p = Get-Process -Id $gamePid -ErrorAction SilentlyContinue
  if ($p -and -not $p.WaitForExit(20000)) { Stop-Process -Id $gamePid -Force; Start-Sleep -Seconds 2 }
  Log "stopped game pid $gamePid; alive now: $(Alive)"
}
[Wn]::CloseDesk()
$col = Join-Path $out 'collected'; New-Item -ItemType Directory -Force $col | Out-Null
foreach ($d in @('Modules\FCCW\Test', 'Modules\FCCW\Saved')) {
  Get-ChildItem -Path (Join-Path $root $d) -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -ge $t0 -and $_.Name.Contains($Scenario) } |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $col (($d -replace '[\\]', '_') + '__' + $_.Name)) }
}
Log 'relay done'
