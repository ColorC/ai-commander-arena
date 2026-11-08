param(
  [string]$Saves = '',                 # ';'-separated autosave copies pushed under ai-arena\in\rf\ (file names), in order
  [string]$RunName = 'vcr-capture',
  [string]$Desktop = 'ai-arena-rf',    # the game on a desktop of its own, out of sight of whoever uses the game machine
  [int]$Width = 1920, [int]$Height = 1080,
  [int]$MapZoomPct = 45,               # flashpoint.ini [General] MapZoomPct for the run: 45 shows all of Marburg in 1920x1080
  [int]$FrameMs = 150,                 # one PrintWindow frame of the main form at most this often while the replay plays
  [int]$Quality = 88,                  # JPEG quality of the frames
  [int]$PlayButton = 4,                # replay window: index (left to right) of the Play button among its TsBitBtn
  [int]$EndStillSec = 6,               # replay over: neither its minute nor the picture has changed for this long
  [int]$ReplayMaxSec = 150,
  [int]$LoadTimeoutSec = 120,          # per save: no replay window by then, give up on that save
  [int]$MaxMinutes = 90
)
# ai-commander-arena raw footage (tools/video/raw_footage/clips.py, build_clip.py): the game's own turn-resolution
# replay (VCR) of every given autosave, captured as frames. Each save is an FPC hot-seat autosave written when a player
# pressed F9; it keeps the replay that player watched, everything resolved since their previous Orders phase, with
# their fog of war (tests 11-08: loading it goes straight to that player's Orders phase, and Game menu > "Replay
# Previous Turn Resolution" then plays that original resolution from the save, units moving and firing; nothing is
# simulated again; F9 instead would resolve a new turn). Per save: a copy goes into Modules\FCCW\Saved as RF-<run>-<tag>.sav, the game is
# started with -Scenario <that file> minimized and not activated on a desktop of its own (CreateDesktop +
# CreateProcess, as relay.ps1 / job-desktop-probe.ps1: nothing reaches the user's desktop, and the job stops its game
# at once if any of its windows shows there), game-flow dialogs are answered by window messages, the Game menu item is
# chosen by posted keys, the replay's Play button pressed, and the main form (shown without activation at Width x
# Height, on that desktop only) is shot with PrintWindow every FrameMs into out\<RunName>\frames\<tag>\NNNNNN.jpg;
# frames-<tag>.tsv has per frame the wall time and the replay minute ("Game resolution replay for minute N", the
# TfGameDescr title). Then the job stops its own game (pid + creation time) and goes on to the next save.
# flashpoint.ini (map zoom) is put back byte for byte at the end, the RF-*.sav copies removed, any autosave the game
# wrote named RF-* moved into out\<RunName>\autosaves (none expected: the job never presses F9), and saved-diff.txt
# lists the Saved folder changes. Writes only under ai-arena\out\<RunName> and those RF-* files.
$ErrorActionPreference = 'Stop'
# game root: FPC_GAME_ROOT, else the job's working directory (jobs are started with the game folder as cwd)
$root = if ($env:FPC_GAME_ROOT) { $env:FPC_GAME_ROOT } else { (Get-Location).Path }
$exe = Join-Path $root 'FlashpointCampaigns.exe'
$inRf = Join-Path $root 'ai-arena\in\rf'
$out = Join-Path $root ('ai-arena\out\' + $RunName)
New-Item -ItemType Directory -Force $out | Out-Null
$jobLog = Join-Path $out 'job.log'
$t0 = Get-Date
function Log([string]$m) { $line = '{0:HH:mm:ss.fff} {1}' -f (Get-Date), $m; Add-Content -Path $jobLog -Value $line -Encoding utf8; Write-Output $line }

Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
using System.Drawing; using System.Drawing.Imaging;
public static class Rf {
  delegate bool EnumProc(IntPtr h, IntPtr l);
  [StructLayout(LayoutKind.Sequential)] struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr CreateDesktop(string name, IntPtr dev, IntPtr dm, uint flags, uint access, IntPtr sa);
  [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr h);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumDesktopWindows(IntPtr d, EnumProc f, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr p, EnumProc f, IntPtr l);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll", SetLastError = true)] static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] static extern bool ShowWindowAsync(IntPtr h, int cmd);
  [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint f);
  [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] static extern int GetSystemMetrics(int i);
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] struct STARTUPINFO {
    public int cb; public string lpReserved, lpDesktop, lpTitle;
    public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
    public short wShowWindow, cbReserved2; public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError; }
  [StructLayout(LayoutKind.Sequential)] struct PROCESS_INFORMATION { public IntPtr hProcess, hThread; public int dwProcessId, dwThreadId; }
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool CreateProcess(string app, StringBuilder cmd, IntPtr pa, IntPtr ta, bool inherit, uint flags, IntPtr env, string cwd, ref STARTUPINFO si, out PROCESS_INFORMATION pi);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
  public static IntPtr Desk = IntPtr.Zero;
  public static string MakeDesk(string name) {
    Desk = CreateDesktop(name, IntPtr.Zero, IntPtr.Zero, 0, 0x000F01FF, IntPtr.Zero);
    return Desk == IntPtr.Zero ? "CreateDesktop failed " + Marshal.GetLastWin32Error() : "ok";
  }
  public static void CloseDesk() { if (Desk != IntPtr.Zero) CloseDesktop(Desk); }
  public static string Screen() { return GetSystemMetrics(0) + "x" + GetSystemMetrics(1); }
  public static string Start(string cmd, string cwd, string desk) {
    var si = new STARTUPINFO(); si.cb = Marshal.SizeOf(si); si.lpDesktop = desk; si.dwFlags = 1; si.wShowWindow = 7;
    PROCESS_INFORMATION pi; string how = "out of the job";
    bool ok = CreateProcess(null, new StringBuilder(cmd), IntPtr.Zero, IntPtr.Zero, false, 0x01000000, IntPtr.Zero, cwd, ref si, out pi);
    if (!ok) { how = "in the job (breakaway refused " + Marshal.GetLastWin32Error() + ")";
      ok = CreateProcess(null, new StringBuilder(cmd), IntPtr.Zero, IntPtr.Zero, false, 0, IntPtr.Zero, cwd, ref si, out pi); }
    if (!ok) return "0\tCreateProcess failed " + Marshal.GetLastWin32Error();
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    return pi.dwProcessId + "\t" + how;
  }
  static string Cls(IntPtr h) { var s = new StringBuilder(256); GetClassName(h, s, 256); return s.ToString(); }
  static string Txt(IntPtr h) { var s = new StringBuilder(2048); GetWindowText(h, s, 2048); return s.ToString().Replace("\r", " ").Replace("\n", " ").Replace("\t", " "); }
  static List<IntPtr> Tops(uint pid, bool own) {
    var o = new List<IntPtr>();
    EnumProc f = (h, l) => { uint p; GetWindowThreadProcessId(h, out p); if (p == pid) o.Add(h); return true; };
    if (own) EnumDesktopWindows(Desk, f, IntPtr.Zero); else EnumWindows(f, IntPtr.Zero);
    return o;
  }
  // windows of the game visible on the user's desktop (this job's own desktop): must stay 0
  public static int VisibleHere(uint pid) { int n = 0; foreach (var h in Tops(pid, false)) if (IsWindowVisible(h)) n++; return n; }
  static IntPtr Find(uint pid, string cls) {
    IntPtr any = IntPtr.Zero;
    foreach (var h in Tops(pid, true)) if (Cls(h) == cls) { if (IsWindowVisible(h)) return h; if (any == IntPtr.Zero) any = h; }
    return any;
  }
  static string Kids(IntPtr top, int max) {
    var k = new List<string>();
    EnumChildWindows(top, (c, l) => { if (IsWindowVisible(c)) { var t = Txt(c); if (t.Length > 0 && k.Count < max) k.Add(t); } return true; }, IntPtr.Zero);
    return string.Join("|", k);
  }
  // visible top-level windows of the game, "class<TAB>title<TAB>child texts"; skip: classes whose children are not read
  public static List<string> Visible(uint pid, string skip) {
    var o = new List<string>();
    foreach (var h in Tops(pid, true)) {
      if (!IsWindowVisible(h)) continue;
      string c = Cls(h);
      o.Add(c + "\t" + Txt(h) + "\t" + (("," + skip + ",").Contains("," + c + ",") ? "" : Kids(h, 60)));
    }
    return o;
  }
  public static string Texts(uint pid, string cls) { IntPtr h = Find(pid, cls); return h == IntPtr.Zero ? "" : Txt(h) + "|" + Kids(h, 200); }
  // all child windows of a form: class, text, rect (diagnosis)
  public static string Tree(uint pid, string cls) {
    IntPtr top = Find(pid, cls); if (top == IntPtr.Zero) return "no " + cls;
    var sb = new StringBuilder();
    EnumChildWindows(top, (c, l) => { RECT r; GetWindowRect(c, out r); sb.AppendLine(c.ToInt64().ToString("x") + "\t" + Cls(c) + "\t" + (IsWindowVisible(c) ? "vis" : "hid") + "\t" + r.L + "," + r.T + "," + r.R + "," + r.B + "\t" + Txt(c)); return true; }, IntPtr.Zero);
    return sb.ToString();
  }
  public static string Click(uint pid, string cls, string text) {
    IntPtr top = Find(pid, cls); if (top == IntPtr.Zero) return "no " + cls;
    IntPtr btn = IntPtr.Zero;
    EnumChildWindows(top, (c, l) => { if (Txt(c) == text) { btn = c; return false; } return true; }, IntPtr.Zero);
    if (btn == IntPtr.Zero) return "no control '" + text + "'";
    return PostMessage(btn, 0x00F5, IntPtr.Zero, IntPtr.Zero) ? "clicked '" + text + "'" : "PostMessage failed " + Marshal.GetLastWin32Error();
  }
  // main form shown without activation (on its own desktop) and placed at 0,0 with the size asked for
  public static string Place(uint pid, string cls, int w, int h) {
    IntPtr top = Find(pid, cls); if (top == IntPtr.Zero) return "no " + cls;
    ShowWindowAsync(top, 4); System.Threading.Thread.Sleep(300);
    bool ok = SetWindowPos(top, IntPtr.Zero, 0, 0, w, h, 0x0004 | 0x0010);   // SWP_NOZORDER | SWP_NOACTIVATE
    RECT r; GetWindowRect(top, out r);
    return "placed " + ok + " " + (r.R - r.L) + "x" + (r.B - r.T) + (IsIconic(top) ? " iconic" : "");
  }
  public static string Minimize(uint pid, string cls) { IntPtr top = Find(pid, cls); return top == IntPtr.Zero ? "no " + cls : ShowWindowAsync(top, 7) + ""; }
  [DllImport("user32.dll")] static extern IntPtr GetMenu(IntPtr h);
  [DllImport("user32.dll")] static extern int GetMenuItemCount(IntPtr m);
  [DllImport("user32.dll")] static extern uint GetMenuItemID(IntPtr m, int i);
  [DllImport("user32.dll")] static extern IntPtr GetSubMenu(IntPtr m, int i);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetMenuString(IntPtr m, uint item, StringBuilder s, int n, uint flags);
  static void Walk(IntPtr m, string pre, StringBuilder sb, string want, ref uint hit) {
    int n = GetMenuItemCount(m);
    for (int i = 0; i < n; i++) {
      var s = new StringBuilder(256); GetMenuString(m, (uint)i, s, 256, 0x400);   // MF_BYPOSITION
      uint id = GetMenuItemID(m, i); string t = s.ToString().Replace("&", "");
      sb.AppendLine(pre + t + "\t" + (id == 0xFFFFFFFF ? "sub" : id.ToString()));
      if (want != null && id != 0xFFFFFFFF && t.StartsWith(want)) hit = id;
      IntPtr sub = GetSubMenu(m, i); if (sub != IntPtr.Zero) Walk(sub, pre + "  ", sb, want, ref hit);
    }
  }
  // the main form's menu bar as text; with want: WM_COMMAND posted for the first item whose caption starts with it
  public static string Menu(uint pid, string cls, string want) {
    IntPtr top = Find(pid, cls); if (top == IntPtr.Zero) return "no " + cls;
    IntPtr m = GetMenu(top); if (m == IntPtr.Zero) return "no menu bar";
    var sb = new StringBuilder(); uint hit = 0; Walk(m, "", sb, want, ref hit);
    if (want != null) sb.Insert(0, hit == 0 ? "no item '" + want + "'\n" : ("WM_COMMAND " + hit + " posted " + PostMessage(top, 0x0111, (IntPtr)hit, IntPtr.Zero) + "\n"));
    return sb.ToString();
  }
  public static string Key(uint pid, string cls, uint vk, bool shift) {
    IntPtr top = Find(pid, cls); if (top == IntPtr.Zero) return "no " + cls;
    Func<uint, long> dn = k => 1 | ((long)MapVirtualKey(k, 0) << 16);
    bool ok = true;
    if (shift) ok &= PostMessage(top, 0x0100, (IntPtr)0x10, (IntPtr)dn(0x10));
    ok &= PostMessage(top, 0x0100, (IntPtr)vk, (IntPtr)dn(vk));
    ok &= PostMessage(top, 0x0101, (IntPtr)vk, (IntPtr)(dn(vk) | (3L << 30)));
    if (shift) ok &= PostMessage(top, 0x0101, (IntPtr)0x10, (IntPtr)(dn(0x10) | (3L << 30)));
    return ok ? "posted" : "PostMessage failed " + Marshal.GetLastWin32Error();
  }
  [DllImport("user32.dll")] static extern uint MapVirtualKey(uint c, uint t);
  [StructLayout(LayoutKind.Sequential)] struct POINT { public int X, Y; }
  [DllImport("user32.dll")] static extern IntPtr ChildWindowFromPointEx(IntPtr h, POINT p, uint f);
  [DllImport("user32.dll")] static extern bool ScreenToClient(IntPtr h, ref POINT p);
  // a left click at (x, y) of the form as Shot captures it, posted to the deepest visible child under it
  public static string Tap(uint pid, string cls, int x, int y) {
    IntPtr top = Find(pid, cls); if (top == IntPtr.Zero) return "no " + cls;
    RECT r; GetWindowRect(top, out r); IntPtr h = top; POINT p;
    while (true) { p.X = r.L + x; p.Y = r.T + y; ScreenToClient(h, ref p); IntPtr c = ChildWindowFromPointEx(h, p, 1); if (c == IntPtr.Zero || c == h) break; h = c; }
    var lp = (IntPtr)((p.Y << 16) | (p.X & 0xFFFF));
    bool ok = PostMessage(h, 0x0200, IntPtr.Zero, lp) && PostMessage(h, 0x0201, (IntPtr)1, lp) && PostMessage(h, 0x0202, IntPtr.Zero, lp);
    return (ok ? "tapped " : "PostMessage failed ") + Cls(h) + " at " + p.X + "," + p.Y;
  }
  // BM_CLICK on the i-th visible child of that class, children ordered left to right (the replay window's buttons)
  public static string ClickIdx(uint pid, string cls, string childCls, int i) {
    IntPtr top = Find(pid, cls); if (top == IntPtr.Zero) return "no " + cls;
    var l = new List<KeyValuePair<int, IntPtr>>();
    EnumChildWindows(top, (c, x) => { if (IsWindowVisible(c) && Cls(c) == childCls) { RECT r; GetWindowRect(c, out r); l.Add(new KeyValuePair<int, IntPtr>(r.L, c)); } return true; }, IntPtr.Zero);
    l.Sort((a, b) => a.Key.CompareTo(b.Key));
    if (i < 0 || i >= l.Count) return "only " + l.Count + " " + childCls;
    return PostMessage(l[i].Value, 0x00F5, IntPtr.Zero, IntPtr.Zero) ? "clicked " + childCls + " #" + i : "PostMessage failed";
  }
  public static string Title(uint pid, string cls) { IntPtr h = Find(pid, cls); return h == IntPtr.Zero ? "" : Txt(h); }
  static ImageCodecInfo Jpg() { foreach (var c in ImageCodecInfo.GetImageEncoders()) if (c.MimeType == "image/jpeg") return c; return null; }
  // PrintWindow of the form into a file (jpg by quality, png when quality <= 0); "w<TAB>h<TAB>ms" or "0<TAB>error"
  public static string Shot(uint pid, string cls, string path, long quality) {
    var sw = System.Diagnostics.Stopwatch.StartNew();
    IntPtr top = Find(pid, cls); if (top == IntPtr.Zero) return "0\tno " + cls;
    RECT r; GetWindowRect(top, out r); int w = r.R - r.L, hh = r.B - r.T;
    if (w <= 0 || hh <= 0) return "0\tempty window";
    using (var bmp = new Bitmap(w, hh, PixelFormat.Format24bppRgb))
    using (var g = Graphics.FromImage(bmp)) {
      IntPtr dc = g.GetHdc(); bool ok = PrintWindow(top, dc, 2); g.ReleaseHdc(dc);
      if (!ok) return "0\tPrintWindow failed";
      // a hash of every 4th pixel of every 4th row: the replay is over when the picture stops changing
      var bd = bmp.LockBits(new Rectangle(0, 0, w, hh), ImageLockMode.ReadOnly, PixelFormat.Format24bppRgb);
      var px = new byte[bd.Stride * hh]; Marshal.Copy(bd.Scan0, px, 0, px.Length); bmp.UnlockBits(bd);
      ulong fnv = 14695981039346656037UL;
      for (int y = 0; y < hh; y += 4) for (int x = 0; x < w; x += 4) { int o = y * bd.Stride + x * 3; fnv = (fnv ^ px[o]) * 1099511628211UL; fnv = (fnv ^ px[o + 1]) * 1099511628211UL; fnv = (fnv ^ px[o + 2]) * 1099511628211UL; }
      if (quality <= 0) bmp.Save(path, ImageFormat.Png);
      else { var ep = new EncoderParameters(1); ep.Param[0] = new EncoderParameter(System.Drawing.Imaging.Encoder.Quality, quality); bmp.Save(path, Jpg(), ep); }
      return w + "\t" + hh + "\t" + sw.ElapsedMilliseconds + "\t" + fnv.ToString("x16");
    }
  }
}
'@

# ---- checks, flashpoint.ini, Saved folder listed ---------------------------------------------------------------------
$list = @($Saves -split ';' | Where-Object { $_ })
foreach ($s in $list) { if (-not (Test-Path -LiteralPath (Join-Path $inRf $s))) { Log "no save $inRf\$s"; exit 2 } }
$savedDir = Join-Path $root 'Modules\FCCW\Saved'
$games = @(Get-CimInstance Win32_Process -Filter "Name='FlashpointCampaigns.exe'" -ErrorAction SilentlyContinue)
if ($games.Count) { Log "another FPC game is running (pid $(($games | ForEach-Object { $_.ProcessId }) -join ',')): not starting"; exit 5 }
function SavedList { Get-ChildItem -LiteralPath $savedDir -File | ForEach-Object { "{0}`t{1}`t{2:o}" -f $_.Name, $_.Length, $_.LastWriteTimeUtc } }
$before = @(SavedList)
$ini = Join-Path $root 'flashpoint.ini'
$iniOrig = [IO.File]::ReadAllBytes($ini)
[IO.File]::WriteAllBytes((Join-Path $out 'flashpoint.ini.orig'), $iniOrig)
$iniText = [Text.Encoding]::Default.GetString($iniOrig)
if ($MapZoomPct -gt 0) { $iniText = $iniText -replace '(?m)^MapZoomPct=\d+', "MapZoomPct=$MapZoomPct" }
[IO.File]::WriteAllText($ini, $iniText, [Text.Encoding]::Default)
$gamePid = 0; $created = $null; $loadPath = $null
function Alive { if (-not $gamePid) { return $false }; $p = Get-CimInstance Win32_Process -Filter "ProcessId=$gamePid" -ErrorAction SilentlyContinue; return [bool]($p -and $p.CreationDate -eq $created) }
function StopGame { if ($gamePid -and (Alive)) { Stop-Process -Id $gamePid -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2; Log "stopped own game pid $gamePid; alive now: $(Alive)" } }
function DropCopy { if ($loadPath -and (Test-Path -LiteralPath $loadPath)) { Remove-Item -LiteralPath $loadPath; Log "removed $loadPath" } }
function RestoreAll {
  try { [IO.File]::WriteAllBytes($ini, $iniOrig); Log 'flashpoint.ini restored' } catch { Log "flashpoint.ini restore failed: $($_.Exception.Message)" }
  try { DropCopy } catch { Log "remove $loadPath failed: $($_.Exception.Message)" }
  $own = Join-Path $out 'autosaves'
  Get-ChildItem -LiteralPath $savedDir -File -Filter 'RF-*' | ForEach-Object {
    New-Item -ItemType Directory -Force $own | Out-Null
    try { Move-Item -LiteralPath $_.FullName -Destination (Join-Path $own $_.Name); Log "moved own autosave $($_.Name)" } catch { Log "move $($_.Name) failed: $($_.Exception.Message)" }
  }
  $diff = @(Compare-Object $before @(SavedList) | ForEach-Object { $_.SideIndicator + ' ' + $_.InputObject })
  $diff | Out-File (Join-Path $out 'saved-diff.txt') -Encoding utf8
  Log "Saved folder changes: $($diff.Count)"
}
trap { Log "error at line $($_.InvocationInfo.ScriptLineNumber): $($_.Exception.Message)"; StopGame; [Rf]::CloseDesk(); RestoreAll; break }

Log "screen $([Rf]::Screen()); desktop ${Desktop}: $([Rf]::MakeDesk($Desktop)); $($list.Count) saves"
if ([Rf]::Desk -eq [IntPtr]::Zero) { RestoreAll; exit 3 }
$panels = 'TacGlowForm,TfGameDescr,TfMiniMap,TApplication,TfSpotlight,TfMain,TfLeader,TfOpMap,TacPngHintWindow,TForm'
$summary = Join-Path $out 'summary.tsv'
"tag`tframes`tfirst_minute`tlast_minute`tresult" | Out-File $summary -Encoding utf8
$deadline = $t0.AddMinutes($MaxMinutes)

# ---- one save: start, ask for the replay, play it, shoot it, stop ------------------------------------------------------
function CaptureOne([string]$file) {
  $tag = [IO.Path]::GetFileNameWithoutExtension($file)
  $frames = Join-Path $out "frames\$tag"; New-Item -ItemType Directory -Force $frames | Out-Null
  $tsv = Join-Path $out "frames-$tag.tsv"
  "idx`twall_ms`tshot_ms`tminute`thash`tdescr" | Out-File $tsv -Encoding utf8
  $script:loadPath = Join-Path $savedDir ("RF-$RunName-$tag.sav")
  if (Test-Path -LiteralPath $loadPath) { Log "$loadPath exists already: skipping $tag"; return "0`t-1`t-1`tskip" }
  Copy-Item -LiteralPath (Join-Path $inRf $file) -Destination $loadPath
  $cmd = '"{0}" -Scenario "{1}" -StyleOfPlay 3 -Quiet 1 -TimedMessages 1' -f $exe, [IO.Path]::GetFileName($loadPath)
  $pidText, $how = ([Rf]::Start($cmd, $root, "WinSta0\$Desktop")) -split "`t"
  $script:gamePid = [int]$pidText
  if (-not $gamePid) { Log "[$tag] game create failed: $how"; DropCopy; return "0`t-1`t-1`tcreate-failed" }
  $script:created = (Get-CimInstance Win32_Process -Filter "ProcessId=$gamePid").CreationDate
  @{ pid = $gamePid; creation = $created.ToString('o'); cmdline = $cmd; desktop = $Desktop } | ConvertTo-Json | Out-File (Join-Path $out "game-pid-$tag.json") -Encoding utf8
  Log "[$tag] game pid $gamePid on $Desktop, $how"
  $s0 = Get-Date; $quietSince = $null; $asks = 0; $nextAsk = Get-Date; $vcrAt = $null; $played = $false; $placed = $false; $plays = 0; $playAt = [datetime]::MinValue; $lastHash = ''; $picSince = Get-Date
  $n = 0; $minute = -1; $firstMin = -1; $moved = $false; $minSince = Get-Date; $nextCheck = Get-Date; $lastDialogs = ''; $result = 'fail'
  while ($true) {
    if ((Get-Date) -ge $nextCheck) {
      if (-not (Alive)) { Log "[$tag] game exited by itself"; $result = 'exited'; break }
      if ([Rf]::VisibleHere([uint32]$gamePid) -gt 0) { Log "[$tag] a game window is visible on the user's desktop: stopping"; StopGame; [Rf]::CloseDesk(); RestoreAll; exit 4 }
      $nextCheck = (Get-Date).AddSeconds(2)
    }
    if ((Get-Date) -ge $deadline) { Log "[$tag] job deadline"; $result = 'deadline'; break }
    $vis = @([Rf]::Visible([uint32]$gamePid, $panels))
    $dlgText = ($vis | Where-Object { ($_ -split "`t")[0] -notin @('TacGlowForm', 'TacPngHintWindow') -and ($_ -split "`t")[0] -notmatch 'UAC' }) -join ' || '
    if ($dlgText -ne $lastDialogs) { Log "[$tag] windows: $dlgText"; $lastDialogs = $dlgText }
    $vcr = $false
    foreach ($v in $vis) {
      $cls, $title, $kids = $v -split "`t", 3
      if ($cls -eq 'TfVCRReplay') { $vcr = $true; continue }
      if ($cls -eq '#32768' -or $cls -eq 'TfTOC_ScnInfo') { continue }   # the information center's Close crashes the game
      if (($cls -notin ($panels -split ',')) -or $title -match 'Pass control|Replay') {
        $btn = @('&Proceed', '&OK', 'OK', '&Yes', '&Close', 'Continue') | Where-Object { ('|' + $kids + '|').Contains('|' + $_ + '|') } | Select-Object -First 1
        if ($btn) { Log "[$tag] dialog $cls '$title' -> $([Rf]::Click([uint32]$gamePid, $cls, $btn))"; Start-Sleep -Milliseconds 400 }
      }
    }
    if (-not $vcrAt) {
      if ($vcr) { $vcrAt = Get-Date; Log "[$tag] replay window up after $([int]($vcrAt - $s0).TotalSeconds) s"; continue }
      # the Orders phase after the load, quiet for 2 s: Game menu > Replay Previous Turn Resolution, the second item
      # (Start/Pause/Resume Turn Resolution, separator, Replay Previous Turn Resolution, Full Map Screen Capture, ...)
      $onlyPanels = -not ($vis | Where-Object { ($_ -split "`t")[0] -notin ($panels -split ',') -and ($_ -split "`t")[0] -notmatch 'UAC' })
      if ($onlyPanels -and ($vis -match '^TfLeader')) { if (-not $quietSince) { $quietSince = Get-Date } } else { $quietSince = $null }
      if ($quietSince -and ((Get-Date) - $quietSince).TotalSeconds -ge 2 -and (Get-Date) -ge $nextAsk -and $asks -lt 3) {
        $asks++
        if (-not $placed) { Log "[$tag] main form: $([Rf]::Place([uint32]$gamePid, 'TfMain', $Width, $Height))"; $placed = $true; Start-Sleep -Milliseconds 800 }
        Log "[$tag] replay request $asks : Game menu $([Rf]::Tap([uint32]$gamePid, 'TfMain', 48, 45))"
        Start-Sleep -Milliseconds 1200
        Log "[$tag] down, down: $([Rf]::Key([uint32]$gamePid, '#32768', 0x28, $false)) $([Rf]::Key([uint32]$gamePid, '#32768', 0x28, $false))"
        Start-Sleep -Milliseconds 400
        Log "[$tag] enter: $([Rf]::Key([uint32]$gamePid, '#32768', 0x0D, $false))"
        $nextAsk = (Get-Date).AddSeconds(15)
      }
      if (((Get-Date) - $s0).TotalSeconds -ge $LoadTimeoutSec) { Log "[$tag] no replay window after $LoadTimeoutSec s"; $result = 'no-replay'; break }
      Start-Sleep -Milliseconds 250
      continue
    }
    # replay: Play, then frames until both its minute and the picture stand still
    if (-not $vcr) { Log "[$tag] replay window gone"; $result = 'ok'; break }
    # Play once the replay shows its first minute and has held it for 1.5 s (a click right after the window comes up
    # is lost: test 11-08 00:53; the title says minute 0 for a moment before the real first minute, which can be 0); again only when neither the minute nor the picture has moved 5 s after a click (a
    # second click on a playing replay pauses it; combat minutes take 10 s and more to play)
    if ($minute -ge 0 -and ((Get-Date) - $minSince).TotalMilliseconds -ge 1500 -and -not $moved -and $plays -lt 3 -and ((Get-Date) - $playAt).TotalSeconds -ge 5) {
      if (-not $placed) { Log "[$tag] main form: $([Rf]::Place([uint32]$gamePid, 'TfMain', $Width, $Height))"; $placed = $true; Start-Sleep -Milliseconds 500 }
      $plays++; $playAt = Get-Date
      Log "[$tag] play $plays : $([Rf]::ClickIdx([uint32]$gamePid, 'TfVCRReplay', 'TsBitBtn', $PlayButton)); descr '$([Rf]::Title([uint32]$gamePid, 'TfGameDescr'))'"
      $played = $true
    }
    $f0 = Get-Date
    $n++
    $r = [Rf]::Shot([uint32]$gamePid, 'TfMain', (Join-Path $frames ('{0:D6}.jpg' -f $n)), $Quality)
    $descr = [Rf]::Title([uint32]$gamePid, 'TfGameDescr')
    $w, $h, $ms, $hash = $r -split "`t"
    $m = [regex]::Match($descr, 'replay for minute (\d+)')
    $mm = if ($m.Success) { [int]$m.Groups[1].Value } else { -1 }
    if ($w -eq '0') { Log "[$tag] frame $n failed: $h"; $n-- }
    else {
      "$n`t$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())`t$ms`t$mm`t$hash`t$descr" | Add-Content -Path $tsv -Encoding utf8
      if ($hash -ne $lastHash) { if ($played -and $lastHash) { $moved = $true }; $lastHash = $hash; $picSince = Get-Date }
    }
    if ($mm -ge 0) {
      if ($firstMin -lt 0) { $firstMin = $mm }
      if ($mm -ne $minute) { if ($played -and $minute -ge 0 -and $mm -gt $minute) { $moved = $true }; $minute = $mm; $minSince = Get-Date }
    }
    $still = [math]::Min(((Get-Date) - $minSince).TotalSeconds, ((Get-Date) - $picSince).TotalSeconds)
    if ($played -and $moved -and $still -ge $EndStillSec) { Log "[$tag] replay at minute $minute, picture unchanged for $EndStillSec s: over"; $result = 'ok'; break }
    if ($plays -ge 3 -and -not $moved -and ((Get-Date) - $playAt).TotalSeconds -ge 10) { Log "[$tag] replay does not move after 3 Play clicks: stop"; $result = 'stuck'; break }
    if (((Get-Date) - $vcrAt).TotalSeconds -ge $ReplayMaxSec) { Log "[$tag] replay longer than $ReplayMaxSec s: stop"; $result = 'long'; break }
    $left = $FrameMs - ((Get-Date) - $f0).TotalMilliseconds
    if ($left -gt 0) { Start-Sleep -Milliseconds ([int]$left) }
  }
  Log "[$tag] frames $n, minutes $firstMin -> $minute, $result"
  StopGame
  DropCopy
  return "$n`t$firstMin`t$minute`t$result"
}

foreach ($s in $list) {
  if ((Get-Date) -ge $deadline) { Log "job deadline: $s not done"; break }
  $res = CaptureOne $s
  "$([IO.Path]::GetFileNameWithoutExtension($s))`t$(@($res)[-1])" | Add-Content -Path $summary -Encoding utf8
  $gamePid = 0; $loadPath = $null
}
[Rf]::CloseDesk()
# the game's debug logs of this run (shared read)
Get-ChildItem -Path (Join-Path $root 'Modules\FCCW\Test') -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -ge $t0 } |
  ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $out ('debug-' + $_.Name)) }
RestoreAll
Log 'done'
