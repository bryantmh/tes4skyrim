param([string]$Pattern)
# Lists "<pid> <name> <command line>" of every process whose command line matches -Pattern (a regex),
# e.g. a stopped test batch's leftover children: find_procs.ps1 "instance 2|Skyrim Test 2"
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -and $_.CommandLine -match $Pattern } |
  ForEach-Object { '{0} {1} {2}' -f $_.ProcessId, $_.Name, $_.CommandLine.Substring(0, [Math]::Min(170, $_.CommandLine.Length)) }
