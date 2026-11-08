param(
  [string]$GameArgs = '-config "arena.ini" -Scenario "Chance Encounter" -Quiet 1 -TimedMessages 1',
  [int[]]$DumpAt = @(25, 60),
  [string]$Tag = 'diag'
)
# Launch FPC minimized with the given arguments, dump this process's windows (incl. hidden ones and
# dialog text) at the given seconds, then stop our own process (pid + creation time).
$ErrorActionPreference = 'Stop'
# game root: FPC_GAME_ROOT, else the job's working directory (jobs are started with the game folder as cwd)
$root = if ($env:FPC_GAME_ROOT) { $env:FPC_GAME_ROOT } else { (Get-Location).Path }
$exe = Join-Path $root 'FlashpointCampaigns.exe'
$run = Join-Path $root ('ai-arena\out\' + $Tag + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force $run | Out-Null
$jobLog = Join-Path $run 'job.log'
$t0 = Get-Date
function Log([string]$m) { $line = '{0:HH:mm:ss} {1}' -f (Get-Date), $m; Add-Content -Path $jobLog -Value $line -Encoding utf8; Write-Output $line }

Add-Type @'
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public static class Wn {
  delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr p, EnumProc f, IntPtr l);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  static string Cls(IntPtr h) { var s = new StringBuilder(256); GetClassName(h, s, 256); return s.ToString(); }
  static string Txt(IntPtr h) { var s = new StringBuilder(2048); GetWindowText(h, s, 2048); return s.ToString(); }
  public static List<string> Dump(uint pid) {
    var o = new List<string>();
    EnumWindows((h, l) => {
      uint p; GetWindowThreadProcessId(h, out p);
      if (p != pid) return true;
      o.Add(string.Format("TOP {0:x} {1} visible={2} \"{3}\"", h.ToInt64(), Cls(h), IsWindowVisible(h), Txt(h)));
      int n = 0;
      EnumChildWindows(h, (c, l2) => {
        if (n++ > 80) return false;
        var t = Txt(c);
        if (t.Length > 0) o.Add(string.Format("    {0} visible={1} \"{2}\"", Cls(c), IsWindowVisible(c), t.Replace("\r", " ").Replace("\n", " | ")));
        return true;
      }, IntPtr.Zero);
      return true;
    }, IntPtr.Zero);
    return o;
  }
}
'@

$cmdline = '"{0}" {1}' -f $exe, $GameArgs
Log "cmdline: $cmdline"
$startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]7 }
$res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmdline; CurrentDirectory = $root; ProcessStartupInformation = $startup }
if ($res.ReturnValue -ne 0) { Log "Win32_Process.Create failed: $($res.ReturnValue)"; exit 3 }
$gamePid = [int]$res.ProcessId
$created = (Get-CimInstance Win32_Process -Filter "ProcessId=$gamePid").CreationDate
Log "game pid $gamePid created $($created.ToString('o'))"
function Game-Alive { $p = Get-CimInstance Win32_Process -Filter "ProcessId=$gamePid" -ErrorAction SilentlyContinue; return ($p -and $p.CreationDate -eq $created) }

foreach ($s in $DumpAt) {
  while (((Get-Date) - $t0).TotalSeconds -lt $s -and (Game-Alive)) { Start-Sleep -Seconds 1 }
  if (-not (Game-Alive)) { Log 'game exited'; break }
  $p = Get-Process -Id $gamePid
  Log ('at {0}s ws={1:N0} MB cpu={2:N1}s' -f $s, ($p.WorkingSet64 / 1MB), $p.CPU)
  $lines = [Wn]::Dump([uint32]$gamePid)
  $lines | Out-File (Join-Path $run "windows-$s.txt") -Encoding utf8
  $lines | ForEach-Object { Log $_ }
}
if (Game-Alive) {
  $proc = Get-Process -Id $gamePid
  $asked = $proc.CloseMainWindow()
  if (-not $proc.WaitForExit(10000)) { Stop-Process -Id $gamePid -Force; Start-Sleep -Seconds 2; Log "CloseMainWindow=$asked; forced" }
  Log "stopped pid $gamePid; alive now: $(Game-Alive)"
}
Get-ChildItem (Join-Path $root 'Modules\FCCW\Test') -File | Where-Object { $_.LastWriteTime -ge $t0 } | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $run; Log "collected $($_.Name)" }
Log 'job done'
