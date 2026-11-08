param(
  [string]$HostName = '127.0.0.1',
  [int]$Port = 47823,
  [string[]]$Lines = @(),
  [int]$DrainMs = 2500,
  [string]$OutFile
)
# One telnet session against the FPC Lua console: read the banner, send each line, capture replies.
$ErrorActionPreference = 'Stop'
$sb = New-Object System.Text.StringBuilder
$latin = [System.Text.Encoding]::GetEncoding(28591)
$client = New-Object System.Net.Sockets.TcpClient
$client.Connect($HostName, $Port)
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
& $drain 2500
foreach ($l in $Lines) {
  [void]$sb.Append("`n>>> $l`n")
  $b = [System.Text.Encoding]::ASCII.GetBytes($l + "`r`n")
  $stream.Write($b, 0, $b.Length)
  & $drain $DrainMs
}
$client.Close()
$text = $sb.ToString()
if ($OutFile) { $text | Out-File $OutFile -Encoding utf8 }
Write-Output $text
