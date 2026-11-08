param(
  [string]$RunName = 'desktop-probe',
  [string]$Scenario = 'Chance Encounter',
  [string]$Desktop = 'ai-arena',
  [int]$RunForMinutes = 30,
  [int]$MaxMinutes = 10
)
# ai-commander-arena: can the game run on a desktop of its own (CreateDesktop in this session's WinSta0), out of sight
# of whoever uses the machine: no window, dialog or taskbar button on their desktop, no focus taken?
# One game started minimized, not activated, on that desktop by CreateProcess (stopped at once if any of its windows
# shows on the user's desktop; Win32_Process.Create, the relay's way, ignores the desktop asked for): the
# British side human (-StyleOfPlay 2 without -config waits in each Orders phase for F9), the Soviet side the game's AI,
# cut to RunForMinutes. Every 10 s: the game's windows on this job's desktop (want none) and on its own
# (EnumDesktopWindows), the debug log's progress; with no progress for 20 s, F9 posted to TfMain (from this thread
# first, from a thread set on the game's desktop when the game did not go on). Once the main form shown without
# activation, PrintWindow shots both ways, then minimized again. Stops its own game (pid + creation time) and closes
# its desktop at the end. Writes only under ai-arena\out\<RunName>.
$ErrorActionPreference = 'Stop'
# game root: FPC_GAME_ROOT, else the job's working directory (jobs are started with the game folder as cwd)
$root = if ($env:FPC_GAME_ROOT) { $env:FPC_GAME_ROOT } else { (Get-Location).Path }
$exe = Join-Path $root 'FlashpointCampaigns.exe'
$out = Join-Path $root ('ai-arena\out\' + $RunName)
New-Item -ItemType Directory -Force $out | Out-Null
$jobLog = Join-Path $out 'job.log'
$t0 = Get-Date
function Log([string]$m) { $line = '{0:HH:mm:ss} {1}' -f (Get-Date), $m; Add-Content -Path $jobLog -Value $line -Encoding utf8; Write-Output $line }

Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System; using System.Text; using System.Threading; using System.Collections.Generic; using System.Runtime.InteropServices;
using System.Drawing; using System.Drawing.Imaging;
public static class Dk {
  delegate bool EnumProc(IntPtr h, IntPtr l);
  [StructLayout(LayoutKind.Sequential)] struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr CreateDesktop(string name, IntPtr dev, IntPtr dm, uint flags, uint access, IntPtr sa);
  [DllImport("user32.dll", SetLastError = true)] static extern bool CloseDesktop(IntPtr h);
  [DllImport("user32.dll", SetLastError = true)] static extern bool SetThreadDesktop(IntPtr h);
  [DllImport("user32.dll")] static extern IntPtr GetThreadDesktop(uint tid);
  [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
  [DllImport("user32.dll", SetLastError = true)] static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
  [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool GetUserObjectInformation(IntPtr h, int index, StringBuilder buf, int len, out int need);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll", SetLastError = true)] static extern bool EnumDesktopWindows(IntPtr d, EnumProc f, IntPtr l);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll", SetLastError = true)] static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] static extern bool ShowWindowAsync(IntPtr h, int cmd);
  [DllImport("user32.dll")] static extern uint MapVirtualKey(uint c, uint t);
  [DllImport("user32.dll", SetLastError = true)] static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] struct STARTUPINFO {
    public int cb; public string lpReserved, lpDesktop, lpTitle;
    public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
    public short wShowWindow, cbReserved2; public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError; }
  [StructLayout(LayoutKind.Sequential)] struct PROCESS_INFORMATION { public IntPtr hProcess, hThread; public int dwProcessId, dwThreadId; }
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool CreateProcess(string app, StringBuilder cmd, IntPtr pa, IntPtr ta, bool inherit, uint flags, IntPtr env, string cwd, ref STARTUPINFO si, out PROCESS_INFORMATION pi);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
  public static IntPtr Desk = IntPtr.Zero;
  // the game on a desktop by CreateProcess (Win32_Process.Create puts it on the default desktop whatever
  // Win32_ProcessStartup.WinstationDesktop says: probe 11-02 15:18); out of this job when the job lets it go
  public static string Start(string cmd, string cwd, string desk, short show) {
    var si = new STARTUPINFO(); si.cb = Marshal.SizeOf(si); si.lpDesktop = desk; si.dwFlags = 1; si.wShowWindow = show;
    PROCESS_INFORMATION pi;
    string how = "out of the job";
    bool ok = CreateProcess(null, new StringBuilder(cmd), IntPtr.Zero, IntPtr.Zero, false, 0x01000000, IntPtr.Zero, cwd, ref si, out pi);
    if (!ok) {
      int e = Marshal.GetLastWin32Error(); how = "in the job (breakaway refused " + e + ")";
      ok = CreateProcess(null, new StringBuilder(cmd), IntPtr.Zero, IntPtr.Zero, false, 0, IntPtr.Zero, cwd, ref si, out pi);
    }
    if (!ok) return "0\tCreateProcess failed " + Marshal.GetLastWin32Error();
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    return pi.dwProcessId + "\t" + how;
  }
  public static int VisibleHere(uint pid) {
    int n = 0;
    foreach (var h in Tops(pid, false)) if (IsWindowVisible(h)) n++;
    return n;
  }
  static string Cls(IntPtr h) { var s = new StringBuilder(256); GetClassName(h, s, 256); return s.ToString(); }
  static string Txt(IntPtr h) { var s = new StringBuilder(512); GetWindowText(h, s, 512); return s.ToString().Replace("\r", " ").Replace("\n", " "); }
  static string Name(IntPtr d) { var s = new StringBuilder(256); int n; return GetUserObjectInformation(d, 2, s, 512, out n) ? s.ToString() : "?" + Marshal.GetLastWin32Error(); }
  public static string Where() {
    IntPtr inp = OpenInputDesktop(0, false, 0x0001);
    string i = inp == IntPtr.Zero ? "? " + Marshal.GetLastWin32Error() : Name(inp);
    if (inp != IntPtr.Zero) CloseDesktop(inp);
    return "this thread's desktop " + Name(GetThreadDesktop(GetCurrentThreadId())) + ", input desktop " + i;
  }
  public static string Make(string name) {
    Desk = CreateDesktop(name, IntPtr.Zero, IntPtr.Zero, 0, 0x000F01FF, IntPtr.Zero);   // all DESKTOP_* rights
    return Desk == IntPtr.Zero ? "CreateDesktop failed " + Marshal.GetLastWin32Error() : "desktop " + Name(Desk) + " made";
  }
  public static bool Close() { return Desk != IntPtr.Zero && CloseDesktop(Desk); }
  // top-level windows of pid: own = on the game's desktop, else on this thread's
  static List<IntPtr> Tops(uint pid, bool own) {
    var o = new List<IntPtr>();
    EnumProc f = (h, l) => { uint p; GetWindowThreadProcessId(h, out p); if (p == pid) o.Add(h); return true; };
    if (own) EnumDesktopWindows(Desk, f, IntPtr.Zero); else EnumWindows(f, IntPtr.Zero);
    return o;
  }
  public static string List(uint pid, bool own) {
    var all = Tops(pid, own); var vis = new List<string>();
    foreach (var h in all) if (IsWindowVisible(h)) vis.Add(Cls(h) + " '" + Txt(h) + "'" + (IsIconic(h) ? " iconic" : ""));
    return all.Count + " windows; visible: " + string.Join("; ", vis);
  }
  static IntPtr Find(uint pid, string cls, bool visibleOnly) {
    foreach (var h in Tops(pid, true)) if (Cls(h) == cls && (!visibleOnly || IsWindowVisible(h))) return h;
    return IntPtr.Zero;
  }
  // f on a fresh thread set on the game's desktop (a thread that has windows cannot change desktop)
  static string OnDesk(Func<string> f) {
    string r = null;
    var t = new Thread(() => { r = SetThreadDesktop(Desk) ? f() : "SetThreadDesktop failed " + Marshal.GetLastWin32Error(); });
    t.Start(); t.Join(20000);
    return r ?? "timeout";
  }
  static string F9(IntPtr top) {
    long scan = MapVirtualKey(0x78, 0), down = 1 | (scan << 16), up = down | (1L << 30) | (1L << 31);
    bool ok = PostMessage(top, 0x0100, (IntPtr)0x78, (IntPtr)down) && PostMessage(top, 0x0101, (IntPtr)0x78, (IntPtr)up);
    return ok ? "posted to " + top.ToInt64() : "PostMessage failed " + Marshal.GetLastWin32Error();
  }
  public static string Key(uint pid, bool onDesk) {
    IntPtr top = Find(pid, "TfMain", false);
    if (top == IntPtr.Zero) return "no TfMain";
    return onDesk ? OnDesk(() => F9(top)) : F9(top);
  }
  public static string Show(uint pid, int cmd) {
    IntPtr top = Find(pid, "TfMain", false);
    return top == IntPtr.Zero ? "no TfMain" : ShowWindowAsync(top, cmd) + "";
  }
  // a visible window of the class (any visible one of pid when cls is empty) closed with WM_CLOSE
  public static string CloseWin(uint pid, string cls, bool onDesk) {
    IntPtr h = IntPtr.Zero;
    foreach (var w in Tops(pid, true)) if (IsWindowVisible(w) && Cls(w) == cls) { h = w; break; }
    if (h == IntPtr.Zero) return "no visible " + cls;
    Func<string> f = () => PostMessage(h, 0x0010, IntPtr.Zero, IntPtr.Zero) ? "WM_CLOSE posted" : "PostMessage failed " + Marshal.GetLastWin32Error();
    return onDesk ? OnDesk(f) : f();
  }
  static string Snap(IntPtr top, string path) {
    RECT r; GetWindowRect(top, out r);
    int w = r.R - r.L, hh = r.B - r.T;
    if (w <= 0 || hh <= 0) return "empty window " + w + "x" + hh;
    using (var bmp = new Bitmap(w, hh, PixelFormat.Format24bppRgb))
    using (var g = Graphics.FromImage(bmp)) {
      IntPtr dc = g.GetHdc(); bool ok = PrintWindow(top, dc, 2); int err = Marshal.GetLastWin32Error(); g.ReleaseHdc(dc);
      if (!ok) return "PrintWindow failed " + err;
      int lit = 0, n = 0; var cols = new HashSet<int>();       // a 40 x 40 grid: share not black, distinct colours
      for (int y = 0; y < 40; y++) for (int x = 0; x < 40; x++) {
        var c = bmp.GetPixel(x * (w - 1) / 39, y * (hh - 1) / 39); n++;
        if (c.R + c.G + c.B > 30) lit++;
        cols.Add(c.ToArgb());
      }
      bmp.Save(path, ImageFormat.Png);
      return w + "x" + hh + ", lit " + (100 * lit / n) + "%, colours " + cols.Count;
    }
  }
  public static string Shot(uint pid, string cls, string path, bool onDesk) {
    IntPtr top = Find(pid, cls, false);
    if (top == IntPtr.Zero) return "no " + cls;
    return onDesk ? OnDesk(() => Snap(top, path)) : Snap(top, path);
  }
}
'@

Log ([Dk]::Where())
$made = [Dk]::Make($Desktop)
Log $made
if (-not [Dk]::Desk.ToInt64()) { exit 2 }
$cmd = '"{0}" -Scenario "{1}" -StyleOfPlay 2 -Quiet 1 -TimedMessages 1 -RunForMinutes {2}' -f $exe, $Scenario, $RunForMinutes
$pidText, $how = ([Dk]::Start($cmd, $root, "WinSta0\$Desktop", 7)) -split "`t"
$gamePid = [int]$pidText
if (-not $gamePid) { Log "game create failed: $how"; [void][Dk]::Close(); exit 3 }
$created = (Get-CimInstance Win32_Process -Filter "ProcessId=$gamePid").CreationDate
Log "game pid $gamePid on WinSta0\$Desktop, $how; $cmd"
function Alive { $p = Get-CimInstance Win32_Process -Filter "ProcessId=$gamePid" -ErrorAction SilentlyContinue; return [bool]($p -and $p.CreationDate -eq $created) }
# a window of the game shown on the user's desktop: stop at once (the point is not to be seen)
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Milliseconds 500
  if ([Dk]::VisibleHere([uint32]$gamePid) -gt 0) {
    Log "visible on the user's desktop: $([Dk]::List([uint32]$gamePid, $false)); stopping"
    Stop-Process -Id $gamePid -Force -ErrorAction SilentlyContinue
    [void][Dk]::Close(); exit 4
  }
}
function Progress {
  $f = Get-ChildItem -Path (Join-Path $root 'Modules\FCCW\Test') -Filter "*DEBUG ~$Scenario~*" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -gt $t0 } | Sort-Object LastWriteTime | Select-Object -Last 1
  if (-not $f) { return 'no debug log yet' }
  $fs = [IO.File]::Open($f.FullName, 'Open', 'Read', 'ReadWrite'); $sr = New-Object IO.StreamReader($fs); $text = $sr.ReadToEnd(); $sr.Close()
  $clocks = [regex]::Matches($text, ' - \((\d\d:\d\d:\d\d)\)')
  return '{0} bytes, {1} Orders Phase lines, last clock {2}' -f $f.Length, ([regex]::Matches($text, 'Orders Phase')).Count, $(if ($clocks.Count) { $clocks[$clocks.Count - 1].Groups[1].Value } else { '-' })
}
trap {
  Log "error at line $($_.InvocationInfo.ScriptLineNumber): $($_.Exception.Message)"
  if ($gamePid -and (Alive)) { Stop-Process -Id $gamePid -Force -ErrorAction SilentlyContinue; Log "stopped own game pid $gamePid" }
  [void][Dk]::Close()
  break
}

$last, $since, $f9 = '', (Get-Date), 0
$shotDone, $dialogSince = $false, @{}
while (((Get-Date) - $t0).TotalMinutes -lt $MaxMinutes) {
  Start-Sleep -Seconds 10
  $sec = [int]((Get-Date) - $t0).TotalSeconds
  if (-not (Alive)) { Log "t+$sec game exited by itself"; break }
  Log "t+$sec user's desktop: $([Dk]::List([uint32]$gamePid, $false))"
  $own = [Dk]::List([uint32]$gamePid, $true)
  Log "t+$sec game's desktop: $own"
  $p = Progress
  Log "t+$sec debug log: $p"
  if ($p -ne $last) { $last, $since = $p, (Get-Date) }
  if (-not $shotDone -and $own -match 'TfMain' -and $sec -ge 40) {
    Log "show main form without activating: $([Dk]::Show([uint32]$gamePid, 4))"
    Start-Sleep -Seconds 3
    Log "shot from this thread: $([Dk]::Shot([uint32]$gamePid, 'TfMain', (Join-Path $out "main-$sec-thread.png"), $false))"
    Log "shot from a thread on the game's desktop: $([Dk]::Shot([uint32]$gamePid, 'TfMain', (Join-Path $out "main-$sec-desk.png"), $true))"
    Log "game's desktop after show: $([Dk]::List([uint32]$gamePid, $true))"
    Log "minimize again: $([Dk]::Show([uint32]$gamePid, 7))"
    $shotDone = $true
  }
  # a dialog left open 30 s: shot, then WM_CLOSE (this thread first, then from the game's desktop)
  foreach ($m in [regex]::Matches($own, '(?:visible: |; )(\w+) ')) {
    $cls = $m.Groups[1].Value
    if ($cls -eq 'TfMain' -or $cls -eq 'TApplication') { continue }
    if (-not $dialogSince.ContainsKey($cls)) { $dialogSince[$cls] = Get-Date; continue }
    $open = ((Get-Date) - $dialogSince[$cls]).TotalSeconds
    if ($open -ge 30) {
      Log "dialog $cls shot: $([Dk]::Shot([uint32]$gamePid, $cls, (Join-Path $out "dialog-$cls-$sec.png"), $false))"
      Log "dialog $cls close: $([Dk]::CloseWin([uint32]$gamePid, $cls, $open -ge 50))"
    }
  }
  foreach ($k in @($dialogSince.Keys)) { if ($own -notmatch "(?:visible: |; )$k ") { $dialogSince.Remove($k) } }
  if (((Get-Date) - $since).TotalSeconds -ge 20) {
    $f9++
    $onDesk = $f9 -gt 2
    Log "t+$sec no progress for 20 s: F9 #$f9 $(if ($onDesk) { 'from the game desktop' } else { 'from this thread' }): $([Dk]::Key([uint32]$gamePid, $onDesk))"
    $since = Get-Date
  }
}
if (Alive) { Stop-Process -Id $gamePid -Force; Log "stopped own game pid $gamePid" }
Log "desktop closed: $([Dk]::Close())"
Log "done; F9 posted $f9 times"
