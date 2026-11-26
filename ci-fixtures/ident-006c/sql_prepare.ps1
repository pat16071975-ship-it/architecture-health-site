# Test-only synthetic SQL administration. All connections are local.
# No real IDENT database, login, password or endpoint is used.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or $env:RUNNER_OS -ne 'Windows' -or $env:GITHUB_REF -ne 'refs/heads/test/ident-006c-sender-20261126'){throw 'DISPOSABLE_RUNNER_REQUIRED'}
$lab=$env:IDENT_LAB_DIR
if(-not $lab -or -not(Test-Path -LiteralPath $lab -PathType Container)){throw 'LAB_DIR'}
$exe=$env:IDENT_SQL_LAB_EXE
if(-not(Test-Path -LiteralPath $exe -PathType Leaf)){throw 'SQL_LAB_EXE'}
$admin=[System.Data.SqlClient.SqlConnection]::new('Data Source=lpc:.\IDENTLAB;Initial Catalog=master;Integrated Security=True;Encrypt=False;TrustServerCertificate=True;Pooling=False;Connect Timeout=10')
function Exec-LabSql([string]$sql){
    $cmd=$admin.CreateCommand()
    try{$cmd.CommandText=$sql;$cmd.CommandTimeout=30;[void]$cmd.ExecuteNonQuery()}finally{$cmd.Dispose()}
}
function Scalar-LabSql([string]$sql){
    $cmd=$admin.CreateCommand()
    try{$cmd.CommandText=$sql;$cmd.CommandTimeout=15;return $cmd.ExecuteScalar()}finally{$cmd.Dispose()}
}
function Read-LabEvents{
    $xml=Scalar-LabSql "SELECT CONVERT(nvarchar(max), t.target_data) FROM sys.dm_xe_session_targets t JOIN sys.dm_xe_sessions s ON s.address=t.event_session_address WHERE s.name=N'Ident006CLab' AND t.target_name=N'ring_buffer'"
    if(-not $xml){throw 'LAB_XE_MISSING'}
    $doc=[xml]$xml
    $events=@($doc.SelectNodes('//event')|Where-Object {$_.SelectSingleNode("action[@name='client_app_name']/value").InnerText -eq 'IDENT-006C read-only pinned service'})
    $logins=@($events|Where-Object {$_.name -eq 'login'}).Count
    $queries=0
    foreach($e in $events){
        if($e.name -ne 'sql_statement_starting'){continue}
        $stmt=$e.SelectSingleNode("data[@name='statement']/value")
        if($stmt -and $stmt.InnerText -match 'COUNT_BIG\s*\(\s*DISTINCT\s+CASE\s+WHEN\s+Payment\s*>\s*0' -and $stmt.InnerText -match 'FROM\s+dbo\.PaymentsIn'){$queries++}
    }
    return @{Logins=$logins;Queries=$queries}
}
function Invoke-Probe([string]$mode){
    & $exe $mode
    if($LASTEXITCODE -ne 0){throw ('SQL_PROBE_FAILED_'+$mode)}
}
function Assert-Count([int]$want,[string]$reason){
    $actual=Scalar-LabSql 'SELECT COUNT_BIG(*) FROM IdentTlsLab.dbo.PaymentsIn'
    if($actual -ne $want){throw $reason}
}
$pw='Aa1!'+[guid]::NewGuid().ToString('N')
Write-Host "::add-mask::$pw"
$first=[DateTime]::UtcNow.AddHours(7).Date.AddDays(-3)
$d0=$first.ToString('yyyy-MM-dd');$d1=$first.AddDays(1).ToString('yyyy-MM-dd');$d3=$first.AddDays(3).ToString('yyyy-MM-dd')
$before=$first.AddDays(-1).ToString('yyyy-MM-dd');$after=$first.AddDays(4).ToString('yyyy-MM-dd')
$admin.Open()
try{
    $ver=Scalar-LabSql "SELECT CONVERT(nvarchar(128),SERVERPROPERTY('ProductVersion'))"
    $edition=Scalar-LabSql "SELECT CONVERT(nvarchar(128),SERVERPROPERTY('Edition'))"
    if($ver -notmatch '^17\.' -or $edition -notmatch 'Express'){throw 'LAB_SQL_ENGINE_IDENTITY'}
    Write-Output "SQL_ENGINE=PASS; VERSION=$ver; EDITION=$edition; DATABASE=SYNTHETIC"
    Exec-LabSql 'CREATE DATABASE IdentTlsLab'
    Exec-LabSql ("CREATE LOGIN ident_lab_reader WITH PASSWORD='"+$pw+"',CHECK_POLICY=OFF,CHECK_EXPIRATION=OFF")
    Exec-LabSql @"
USE IdentTlsLab;
CREATE TABLE dbo.PaymentsIn(DatePayment date NOT NULL, ID_Patients int NOT NULL, Payment decimal(18,2) NOT NULL);
INSERT dbo.PaymentsIn VALUES ('$d0',1,120.05),('$d0',2,-10.05),('$d1',3,25.00),('$d3',4,12.34),('$d3',4,0.66),('$before',9,999.00),('$after',9,777.00);
CREATE USER ident_lab_reader FOR LOGIN ident_lab_reader;
GRANT SELECT ON dbo.PaymentsIn TO ident_lab_reader;
"@
    Exec-LabSql @'
CREATE EVENT SESSION Ident006CLab ON SERVER ADD EVENT sqlserver.login(ACTION(sqlserver.client_app_name,sqlserver.server_principal_name,sqlserver.session_id)), ADD EVENT sqlserver.sql_statement_starting(ACTION(sqlserver.client_app_name,sqlserver.server_principal_name,sqlserver.session_id)) ADD TARGET package0.ring_buffer(SET max_memory=4096) WITH (MAX_DISPATCH_LATENCY=1 SECONDS);
ALTER EVENT SESSION Ident006CLab ON SERVER STATE=START;
'@
    [IO.File]::WriteAllText((Join-Path $lab 'first-day.txt'),$d0,[Text.Encoding]::ASCII)
    [IO.File]::WriteAllText((Join-Path $lab 'sql-password.synthetic'),$pw,[Text.Encoding]::ASCII)
    foreach($name in @('good','wrong')){New-Item -ItemType Directory -Path (Join-Path $lab $name)|Out-Null}
    $good=Join-Path $lab 'good\server.cer'
    & python.exe (Join-Path $env:IDENT_WORK 'tests/sql_capture.py') $good
    if($LASTEXITCODE -ne 0){throw 'SQL_CERT_OBSERVATION_FAILED'}
    $cert=[Security.Cryptography.X509Certificates.X509Certificate2]::new($good)
    try{
        if($cert.Subject -notmatch 'SSL_Self_Signed_Fallback' -or $cert.Subject -ne $cert.Issuer){throw 'EXPECTED_FALLBACK_CERT'}
        Write-Output 'SQL_TLS_FALLBACK_CERT=PASS; OBSERVED_BEFORE_LOGIN=YES'
    }finally{$cert.Dispose()}
    $wrong=New-SelfSignedCertificate -DnsName 'ident-sql-lab-wrong.invalid' -CertStoreLocation 'Cert:\CurrentUser\My' -NotAfter (Get-Date).AddDays(1)
    try{[IO.File]::WriteAllBytes((Join-Path $lab 'wrong\server.cer'),$wrong.RawData)}
    finally{Remove-Item -LiteralPath ('Cert:\CurrentUser\My\'+$wrong.Thumbprint)}
    if((Get-FileHash -LiteralPath $good).Hash -eq (Get-FileHash -LiteralPath (Join-Path $lab 'wrong\server.cer')).Hash){throw 'WRONG_CERT_EQUAL'}
    Invoke-Probe 'positive'
    Start-Sleep -Seconds 3
    $baseline=Read-LabEvents
    if($baseline.Logins -lt 1 -or $baseline.Queries -lt 1){throw 'SQL_POSITIVE_EVIDENCE_MISSING'}
    Write-Output ('SQL_EVENT_POSITIVE=PASS; LOGINS='+$baseline.Logins+'; FINANCIAL_QUERIES='+$baseline.Queries)
    Invoke-Probe 'wrong-pin'
    Invoke-Probe 'wrong-hash'
    Start-Sleep -Seconds 3
    $negative=Read-LabEvents
    if($negative.Logins -ne $baseline.Logins -or $negative.Queries -ne $baseline.Queries){throw 'WRONG_PIN_REACHED_SQL_QUERY_OR_LOGIN'}
    Write-Output 'SQL_TLS_NEGATIVE=PASS; WRONG_VALID_DER=REJECTED; WRONG_HASH=REJECTED; NEW_SUCCESSFUL_LOGIN=NO; FINANCIAL_QUERY=NO'
    Exec-LabSql 'USE IdentTlsLab; GRANT INSERT ON dbo.PaymentsIn TO ident_lab_reader;'
    Invoke-Probe 'extra-right'
    Start-Sleep -Seconds 3
    $extra=Read-LabEvents
    if($extra.Logins -le $negative.Logins -or $extra.Queries -ne $negative.Queries){throw 'READONLY_NEGATIVE_EVIDENCE'}
    Assert-Count 7 'SQL_TABLE_CHANGED'
    Write-Output 'SQL_READONLY_NEGATIVE=PASS; EXTRA_INSERT_GRANT=REJECTED; FINANCIAL_QUERY=NO; TABLE_UNCHANGED=YES'
    # Keep the intentionally unsafe synthetic grant until the actual SCM service
    # tests it. It is revoked by the service-lab controller before recovery.
    [IO.File]::WriteAllText((Join-Path $lab 'probe-baseline.json'),($extra|ConvertTo-Json -Compress),[Text.Encoding]::ASCII)
    Write-Output 'SQL_PROBE_SUITE=PASS; PRODUCTION=NONE'
}finally{$admin.Dispose();$pw=$null}
