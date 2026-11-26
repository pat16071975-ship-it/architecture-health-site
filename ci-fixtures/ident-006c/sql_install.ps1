# Only a fresh GitHub-hosted Windows runner. Never run on a clinic host.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or $env:GITHUB_REF -ne 'refs/heads/test/ident-006c-sender-20261126') { throw 'LAB_RUNNER_REQUIRED' }
if (Get-Service -Name 'MSSQL$IDENTLAB' -ErrorAction SilentlyContinue) { throw 'LAB_INSTANCE_ALREADY_EXISTS' }
$lab = Join-Path $env:RUNNER_TEMP 'ident-tls-lab'
New-Item -ItemType Directory -Path $lab -ErrorAction Stop | Out-Null
$media = Join-Path $lab 'SQLEXPR_x64_ENU.exe'
$extract = Join-Path $lab 'sql-media'
$ProgressPreference = 'SilentlyContinue'
Invoke-WebRequest -Uri 'https://download.microsoft.com/download/dea8c210-c44a-4a9d-9d80-0c81578860c5/ENU/SQLEXPR_x64_ENU.exe' -OutFile $media -TimeoutSec 300
$sig = Get-AuthenticodeSignature -LiteralPath $media
if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Microsoft Corporation') { throw 'SQL_MEDIA_SIGNATURE' }
Write-Host ('SQL_MEDIA_SIGNATURE=PASS; SHA256=' + (Get-FileHash -LiteralPath $media -Algorithm SHA256).Hash)
$p = Start-Process -FilePath $media -ArgumentList @('/q',('/x:"' + $extract + '"')) -Wait -PassThru
if ($p.ExitCode -ne 0) { throw ('SQL_EXTRACT=' + $p.ExitCode) }
$setup = Join-Path $extract 'setup.exe'
$sig = Get-AuthenticodeSignature -LiteralPath $setup
if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Microsoft Corporation') { throw 'SQL_SETUP_SIGNATURE' }
$account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = '/Q /ACTION=Install /FEATURES=SQLENGINE /INSTANCENAME=IDENTLAB /SQLSYSADMINACCOUNTS="' + $account + '" /TCPENABLED=0 /NPENABLED=0 /SQLSVCSTARTUPTYPE=Manual /BROWSERSVCSTARTUPTYPE=Disabled /UpdateEnabled=False /IACCEPTSQLSERVERLICENSETERMS /SUPPRESSPRIVACYSTATEMENTNOTICE'
$p = Start-Process -FilePath $setup -ArgumentList $arguments -Wait -PassThru
if ($p.ExitCode -notin @(0,3010)) {
    $log = Get-ChildItem 'C:\Program Files\Microsoft SQL Server\*\Setup Bootstrap\Log\Summary.txt' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($log) { Get-Content -LiteralPath $log.FullName -Tail 70 }
    throw ('SQL_INSTALL_EXIT=' + $p.ExitCode)
}
$instance = (Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server\Instance Names\SQL').IDENTLAB
if ($instance -notmatch '^MSSQL17\.IDENTLAB$') { throw ('LAB_INSTANCE_UNEXPECTED: ' + $instance) }
$base = 'HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server\' + $instance + '\MSSQLServer'
$net = Join-Path $base 'SuperSocketNetLib'
$tcp = Join-Path $net 'Tcp'
Stop-Service -Name 'MSSQL$IDENTLAB' -Force
Set-ItemProperty -LiteralPath $base -Name LoginMode -Value 2
Set-ItemProperty -LiteralPath $net -Name Certificate -Value ''
Set-ItemProperty -LiteralPath $net -Name ForceEncryption -Value 0
Set-ItemProperty -LiteralPath $tcp -Name Enabled -Value 1
Set-ItemProperty -LiteralPath $tcp -Name ListenOnAllIPs -Value 0
$loopback = 0
foreach ($key in Get-ChildItem -LiteralPath $tcp | Where-Object { $_.PSChildName -match '^IP[0-9]+$' }) {
    $props = Get-ItemProperty -LiteralPath $key.PSPath
    $isLoopback = $props.IpAddress -eq '127.0.0.1'
    Set-ItemProperty -LiteralPath $key.PSPath -Name Enabled -Value ([int]$isLoopback)
    Set-ItemProperty -LiteralPath $key.PSPath -Name TcpDynamicPorts -Value ''
    if ($isLoopback) { Set-ItemProperty -LiteralPath $key.PSPath -Name TcpPort -Value '15433'; $loopback++ }
}
if ($loopback -ne 1) { throw 'SQL_LOOPBACK_CONFIG' }
Set-ItemProperty -LiteralPath (Join-Path $tcp 'IPAll') -Name TcpDynamicPorts -Value ''
Set-ItemProperty -LiteralPath (Join-Path $tcp 'IPAll') -Name TcpPort -Value '15433'
Start-Service -Name 'MSSQL$IDENTLAB'
$svc = Get-CimInstance Win32_Service | Where-Object Name -eq 'MSSQL$IDENTLAB'
$deadline = (Get-Date).AddSeconds(90)
do {
    $listeners = @(Get-NetTCPConnection -State Listen -OwningProcess $svc.ProcessId -ErrorAction SilentlyContinue)
    if (@($listeners | Where-Object { $_.LocalAddress -notin @('127.0.0.1','::1') }).Count -ne 0) { Stop-Service -Name 'MSSQL$IDENTLAB' -Force; throw 'NON_LOOPBACK_SQL_LISTENER' }
    if (@($listeners | Where-Object { $_.LocalAddress -eq '127.0.0.1' -and $_.LocalPort -eq 15433 }).Count -eq 1) { break }
    Start-Sleep -Seconds 1
} while ((Get-Date) -lt $deadline)
if (@($listeners | Where-Object { $_.LocalAddress -eq '127.0.0.1' -and $_.LocalPort -eq 15433 }).Count -ne 1) { throw 'SQL_LOCAL_LISTENER_MISSING' }
"IDENT_LAB_DIR=$lab" >> $env:GITHUB_ENV
Write-Host 'SQL_LAB_INSTALL=PASS; LOOPBACK_ONLY=YES; SQL_PORT=15433; PRODUCTION=NONE'
