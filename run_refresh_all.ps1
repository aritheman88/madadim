# Wrapper for the monthly Task Scheduler job "\madadim\madadim data refresh".
# Runs refresh_all.py (refresh every pre-fetched data file, commit + push what
# actually changed), tees all output to a per-run log, prunes logs older than
# 90 days. Exit code is the script's exit code so Task Scheduler's
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
$log = Join-Path $logDir ('refresh_all_{0:yyyy-MM-dd_HH-mm-ss}.log' -f (Get-Date))

Set-Location $dir
("[{0}] starting: {1} -u refresh_all.py" -f (Get-Date), $py) |
    Tee-Object -FilePath $log

& $py -u refresh_all.py *>&1 | Tee-Object -FilePath $log -Append
$rc = $LASTEXITCODE

("[{0}] exit code {1}" -f (Get-Date), $rc) | Tee-Object -FilePath $log -Append

Get-ChildItem $logDir -Filter 'refresh_all_*.log' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-90) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

exit $rc
