# Wrapper for the monthly Task Scheduler job "\madadim\madadim data refresh".
# Runs refresh_all.py (refresh every pre-fetched data file, commit + push what
# actually changed), writes a clean UTF-8 per-run log, prunes logs older than
# 90 days. Exit code is refresh_all.py's own exit code so Task Scheduler's
# "Last Run Result" is meaningful:
#   0 = all fetch jobs OK (with or without data changes)
#   1 = ran, but one or more fetch jobs failed (the rest were still committed)
#   2 = git commit/push failed
#   3 = working tree was dirty before the run - nothing done

$ErrorActionPreference = 'Continue'

$dir    = 'C:\Users\Ariel\PycharmProjects\MyPythonScripts\cbs'
$py     = 'C:\Users\Ariel\anaconda3\python.exe'
$logDir = Join-Path $dir 'logs'

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp   = '{0:yyyy-MM-dd_HH-mm-ss}' -f (Get-Date)
$log     = Join-Path $logDir "refresh_all_$stamp.log"
$outTmp  = Join-Path $logDir "refresh_all_$stamp.out.tmp"
$errTmp  = Join-Path $logDir "refresh_all_$stamp.err.tmp"

# Start-Process with separate redirect files avoids PowerShell 5.1 wrapping the
# child's stderr lines as NativeCommandError records (which would also flip $?).
$p = Start-Process -FilePath $py `
                   -ArgumentList '-u', 'refresh_all.py' `
                   -WorkingDirectory $dir `
                   -NoNewWindow -Wait -PassThru `
                   -RedirectStandardOutput $outTmp `
                   -RedirectStandardError  $errTmp
$rc = $p.ExitCode

$header = "[$stamp] $py -u refresh_all.py  (exit $rc)"
$body   = @(Get-Content -LiteralPath $outTmp -ErrorAction SilentlyContinue)
$errs   = @(Get-Content -LiteralPath $errTmp -ErrorAction SilentlyContinue)
if ($errs.Count) { $body += ''; $body += '----- stderr -----'; $body += $errs }
,($header) + $body | Set-Content -LiteralPath $log -Encoding utf8
Remove-Item -LiteralPath $outTmp, $errTmp -Force -ErrorAction SilentlyContinue

Get-ChildItem $logDir -Filter 'refresh_all_*.log' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-90) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

exit $rc
