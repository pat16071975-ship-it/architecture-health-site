# Disposable Windows PowerShell 5.1 controller for the unmodified installed SCM service.
param([Parameter(Mandatory=$true)][string]$HttpLab,[Parameter(Mandatory=$true)][string]$ManifestSha,[Parameter(Mandatory=$true)][string]$MachineSha)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or $env:RUNNER_OS -ne 'Windows' -or $env:GITHUB_REF -ne 'refs/heads/test/ident-006c-sender-20261126' -or $PSVersionTable.PSEdition -ne 'Desktop'){throw 'DISPOSABLE_RUNNER_REQUIRED'}
$root=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)) 'AZ-IDENT006C'
$journal=Join-Path $root 'state\journal'
$exe=Join-Path $root 'app\AzIdentSender.exe'
$state=Join-Path $root 'state\status.json'
$packet2=Join-Path $journal 'packet-0000000000000002.bin'
$ack2=Join-Path $journal 'ack-0000000000000002.bin'
$ack1=Join-Path $journal 'ack-0000000000000001.bin'
$sqlLab=$env:IDENT_LAB_DIR
$testExe=$env:IDENT_SQL_LAB_EXE
$installer=Join-Path $env:IDENT_WORK 'deploy\install.ps1'
$base=@('-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$installer,'-ExpectedComputerName',$env:COMPUTERNAME,'-ExpectedMachineGuidSha256',$MachineSha,'-ManifestSha256',$ManifestSha)
function Step([string[]]$Rest){
    & powershell.exe @base @Rest
    if($LASTEXITCODE -ne 0){throw 'INSTALLER_STEP_FAILED'}
}
function Await([scriptblock]$Condition,[int]$Seconds,[string]$Reason){
    $until=[DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        if(Test-Path -LiteralPath (Join-Path $HttpLab 'receiver-error')){throw 'SYNTHETIC_RECEIVER_REJECTED_PACKET'}
        if(& $Condition){return}
        Start-Sleep -Milliseconds 250
    }while([DateTime]::UtcNow -lt $until)
    throw $Reason
}
function Status-Is([string]$Code,[long]$Local,[long]$Ack){
    try {
        $o=Get-Content -LiteralPath $state -Raw|ConvertFrom-Json
        return ($o.status -ceq $Code -and $o.local_sequence -eq $Local -and $o.acked_sequence -eq $Ack)
    } catch {return $false}
}
function Assert-NoPacket2 {
    if((Test-Path -LiteralPath $packet2) -or (Test-Path -LiteralPath $ack2) -or @(Get-ChildItem -LiteralPath $journal -File -Filter 'packet-*').Count -ne 1){throw 'UNEXPECTED_PACKET_ON_SQL_FAILURE'}
}
$admin=[System.Data.SqlClient.SqlConnection]::new('Data Source=lpc:.\IDENTLAB;Initial Catalog=master;Integrated Security=True;Encrypt=False;TrustServerCertificate=True;Pooling=False;Connect Timeout=10')
function Exec-LabSql([string]$sql){
    $c=$admin.CreateCommand()
    try{$c.CommandText=$sql;$c.CommandTimeout=30;[void]$c.ExecuteNonQuery()}finally{$c.Dispose()}
}
function Scalar-LabSql([string]$sql){
    $c=$admin.CreateCommand()
    try{$c.CommandText=$sql;$c.CommandTimeout=15;return $c.ExecuteScalar()}finally{$c.Dispose()}
}
function Read-LabEvents{
    $xml=Scalar-LabSql "SELECT CONVERT(nvarchar(max),t.target_data) FROM sys.dm_xe_session_targets t JOIN sys.dm_xe_sessions s ON s.address=t.event_session_address WHERE s.name=N'Ident006CLab' AND t.target_name=N'ring_buffer'"
    if(-not $xml){throw 'LAB_XE_MISSING'}
    $doc=[xml]$xml
    if($doc.DocumentElement.GetAttribute('truncated') -eq '1'){throw 'LAB_XE_TRUNCATED'}
    $events=@($doc.SelectNodes('//event')|Where-Object {
        $app=$_.SelectSingleNode("action[@name='client_app_name']/value")
        $app -and $app.InnerText -ceq 'IDENT-006C read-only pinned service'
    })
    $logins=@($events|Where-Object {$_.GetAttribute('name') -ceq 'login'}).Count
    $queries=0
    foreach($e in $events){
        if($e.GetAttribute('name') -notin @('sql_statement_starting','sp_statement_starting','rpc_starting','sql_batch_starting')){continue}
        # SqlCommand's parameterized batch can arrive as RPC / sp_executesql.
        # Count events (not executions) that carry the distinctive exact
        # aggregate in a statement, batch or sql_text action.
        $pieces=@($e.SelectNodes("data[@name='statement' or @name='batch_text']/value | action[@name='sql_text']/value")|ForEach-Object {$_.InnerText})
        $stmt=$pieces -join ' '
        if($stmt -match 'COUNT_BIG\s*\(\s*DISTINCT\s+CASE\s+WHEN\s+Payment\s*>\s*0' -and $stmt -match 'FROM\s+dbo\.PaymentsIn'){$queries++}
    }
    Write-Host ('XE_EVIDENCE; LOGINS='+$logins+'; AGGREGATE_EVENTS='+$queries+'; EVENT_TYPES='+(@($events|Group-Object {$_.GetAttribute('name')}|ForEach-Object {$_.Name+':'+$_.Count}) -join ','))
    return @{Logins=$logins;Queries=$queries}
}
$server=$null
$admin.Open()
try{
    $first=(Get-Content -LiteralPath (Join-Path $sqlLab 'first-day.txt') -Raw).Trim()
    if($first -cne [DateTime]::UtcNow.AddHours(7).Date.AddDays(-3).ToString('yyyy-MM-dd')){throw 'FIXTURE_DATE_ROLLOVER'}
    $sourceBefore=Scalar-LabSql "SELECT DatePayment,ID_Patients,Payment FROM IdentTlsLab.dbo.PaymentsIn ORDER BY DatePayment,ID_Patients,Payment FOR JSON PATH"
    & $testExe provision
    if($LASTEXITCODE -ne 0){throw 'SQL_SERVICE_PROVISION_FAILED'}
    Remove-Item -LiteralPath (Join-Path $sqlLab 'sql-password.synthetic') -Force
    & $exe --check-local
    if($LASTEXITCODE -ne 0){throw 'INSTALLED_LOCAL_CHECK_FAILED'}
    $py=(Get-Command python.exe -CommandType Application|Select-Object -First 1).Source
    $server=Start-Process -FilePath $py -ArgumentList @('-B',('"'+(Join-Path $PSScriptRoot 'sql_receiver.py')+'"'),('"'+$HttpLab+'"')) -PassThru -NoNewWindow -RedirectStandardOutput (Join-Path $HttpLab 'server.stdout') -RedirectStandardError (Join-Path $HttpLab 'server.stderr')
    Await {Test-Path -LiteralPath (Join-Path $HttpLab 'https-ready')} 30 'HTTPS_FIXTURE_START'
    # SQL permission violation in the real service. The old seed may be
    # delivered, but no SQL financial packet may be created or transmitted.
    $baseline=Get-Content -LiteralPath (Join-Path $sqlLab 'probe-baseline.json') -Raw|ConvertFrom-Json
    Step @('-Mode','Enable','-Confirm','IDENT-006C-ENABLE')
    Await {(Test-Path -LiteralPath $ack1) -and (Status-Is 'BLOCKED_SQL_READONLY_GUARD_FAILED' 1 1)} 55 'SCM_READONLY_GUARD_NOT_OBSERVED'
    Assert-NoPacket2
    Start-Sleep -Seconds 3
    $rejected=Read-LabEvents
    if($rejected.Logins -le $baseline.Logins -or $rejected.Queries -ne $baseline.Queries){throw 'SCM_READONLY_QUERY_EVIDENCE'}
    Write-Output 'SCM_SQL_READONLY=PASS; EXTRA_INSERT_GRANT=BLOCKED; NO_FINANCIAL_QUERY=YES; NO_PACKET=YES'
    Step @('-Mode','Disable','-Confirm','IDENT-006C-DISABLE')
    Exec-LabSql 'USE IdentTlsLab; REVOKE INSERT ON dbo.PaymentsIn FROM ident_lab_reader;'
    # Block a genuine SQL read until its bounded command times out, proving no
    # false zero. Release lock only after the actual service reports retry.
    $locker=[System.Data.SqlClient.SqlConnection]::new('Data Source=lpc:.\IDENTLAB;Initial Catalog=IdentTlsLab;Integrated Security=True;Encrypt=False;TrustServerCertificate=True;Pooling=False')
    $locker.Open()
    $tx=$locker.BeginTransaction()
    $lc=$locker.CreateCommand()
    $lc.Transaction=$tx;$lc.CommandText='SELECT COUNT_BIG(*) FROM dbo.PaymentsIn WITH (TABLOCKX,HOLDLOCK)'
    [void]$lc.ExecuteScalar()
    try {
        Step @('-Mode','Enable','-Confirm','IDENT-006C-ENABLE')
        $enc=$null
        $until=[DateTime]::UtcNow.AddSeconds(18)
        do {
            $enc=Scalar-LabSql "SELECT TOP (1) CONCAT(c.encrypt_option,':',c.auth_scheme,':',c.net_transport,':',c.client_net_address) FROM sys.dm_exec_connections c JOIN sys.dm_exec_sessions s ON s.session_id=c.session_id WHERE s.login_name=N'ident_lab_reader' AND s.program_name=N'IDENT-006C read-only pinned service'"
            if($enc){break}
            Start-Sleep -Milliseconds 100
        }while([DateTime]::UtcNow -lt $until)
        if($enc -cne 'TRUE:SQL:TCP:127.0.0.1'){throw 'SCM_SQL_ENCRYPTION_NOT_PROVEN'}
        Write-Output ('SCM_SQL_TLS_DMV=PASS; '+$enc)
        Await {Status-Is 'SQL_CONNECTION_OR_QUERY' 1 1} 50 'SCM_SQL_TIMEOUT_NOT_OBSERVED'
        Assert-NoPacket2
        Write-Output 'SCM_SQL_TIMEOUT=PASS; NO_FALSE_ZERO=YES; NO_NEW_PACKET=YES'
    }finally{$tx.Rollback();$lc.Dispose();$tx.Dispose();$locker.Dispose()}
    # Receiver rejects first new packet with 503; the real sender must have
    # collected a new exact SQL window, filled only successful missing day.
    Await {(Test-Path -LiteralPath (Join-Path $HttpLab 'receiver-503')) -and (Test-Path -LiteralPath $packet2) -and (Status-Is 'RECEIVER_TEMPORARY_FAILURE' 2 1)} 85 'SCM_SQL_RECOVERY_OR_QUEUE_MISSING'
    if(Test-Path -LiteralPath $ack2){throw 'ACK_WITHOUT_COMMIT'}
    if(Test-Path -LiteralPath (Join-Path $HttpLab 'commit-2')){throw 'EARLY_COMMIT'}
    $cipherHash=(Get-FileHash -LiteralPath $packet2 -Algorithm SHA256).Hash
    Write-Output 'SCM_SQL_AUTOMATIC_RECOVERY=PASS; EXACT_FOUR_DAY_AGGREGATES=PASS; ZERO_DAY=PASS; ONE_PENDING=YES; RECEIVER_503=PASS'
    [IO.File]::WriteAllText((Join-Path $HttpLab 'allow-2'),'1')
    Await {Test-Path -LiteralPath (Join-Path $HttpLab 'commit-2')} 70 'SQL_NEW_PACKET_NOT_COMMITTED'
    if(Test-Path -LiteralPath $ack2){throw 'ACK_WAS_NOT_LOST'}
    $pidBefore=(Get-CimInstance Win32_Service -Filter "Name='AzIdent006C'").ProcessId
    if($pidBefore -le 0){throw 'SCM_PID'}
    Stop-Process -Id $pidBefore -Force
    Await {
        $s=Get-CimInstance Win32_Service -Filter "Name='AzIdent006C'"
        $s.State -eq 'Running' -and $s.ProcessId -gt 0 -and $s.ProcessId -ne $pidBefore
    } 100 'SCM_RESTART_FAILED'
    Await {(Test-Path -LiteralPath $ack2) -and (Test-Path -LiteralPath (Join-Path $HttpLab 'duplicate-2'))} 70 'DURABLE_NEW_ACK_MISSING'
    if((Get-FileHash -LiteralPath $packet2 -Algorithm SHA256).Hash -cne $cipherHash -or @(Get-ChildItem -LiteralPath $journal -File -Filter 'packet-*').Count -ne 2){throw 'QUEUE_CHANGED_OR_DUPLICATE'}
    Await {(Status-Is 'ACK_PERSISTED' 2 2) -or (Status-Is 'IDLE' 2 2)} 10 'FINAL_SEQUENCE_STATUS'
    Step @('-Mode','Disable','-Confirm','IDENT-006C-DISABLE')
    & $testExe journal
    if($LASTEXITCODE -ne 0){throw 'JOURNAL_VERIFICATION_FAILED'}
    $sourceAfter=Scalar-LabSql "SELECT DatePayment,ID_Patients,Payment FROM IdentTlsLab.dbo.PaymentsIn ORDER BY DatePayment,ID_Patients,Payment FOR JSON PATH"
    if($sourceAfter -cne $sourceBefore){throw 'SQL_SOURCE_CHANGED'}
    Start-Sleep -Seconds 2
    $finalEvents=Read-LabEvents
    if($finalEvents.Queries -le $rejected.Queries){throw 'SCM_FINANCIAL_QUERY_NOT_PROVEN'}
    # Independently inspect the reference store, exact committed body and
    # replacement of the overlapping seed day instead of adding it again.
    $env:IDENT_SQL_HTTP_LAB=$HttpLab
    @'
import os,json,sqlite3,hashlib
from decimal import Decimal
from pathlib import Path
p=Path(os.environ["IDENT_SQL_HTTP_LAB"])
with sqlite3.connect((p/"lab.sqlite3").as_uri()+"?mode=ro",uri=True) as db:
    batches=db.execute("select sequence,payload_sha256,payload from batches order by sequence").fetchall()
    assert [r[0] for r in batches]==[1,2]
    assert batches[0][2]==(p/"seed.json").read_bytes()
    assert batches[1][2]==(p/"accepted-2.json").read_bytes()
    assert batches[1][1]==hashlib.sha256(batches[1][2]).hexdigest()==(p/"attempt-2.sha256").read_text()
    daily=db.execute("select sequence,records_count,paying_patients,positive_amount,negative_amount,net_amount from daily order by date").fetchall()
    assert [r[0] for r in daily]==[2,2,2,2]
    assert [(r[1],r[2],*(Decimal(v) for v in r[3:])) for r in daily]==[(2,1,Decimal("120.05"),Decimal("-10.05"),Decimal("110")),(1,1,Decimal(25),Decimal(0),Decimal(25)),(0,0,Decimal(0),Decimal(0),Decimal(0)),(2,1,Decimal(13),Decimal(0),Decimal(13))]
    assert sum(Decimal(r[5]) for r in daily)==Decimal(148)
print("RECEIVER_FINAL=PASS; TWO_BATCHES=YES; FOUR_DAYS=YES; NET=148.00_SYNTHETIC; NO_DOUBLE_COUNT=YES")
'@ | & $py -
    if($LASTEXITCODE -ne 0){throw 'RECEIVER_FINAL_VALIDATION_FAILED'}
    Write-Output 'SCM_SQL_SOURCE_UNCHANGED=PASS; EXACT_RETRY=PASS; LOST_ACK=PASS; SCM_AUTOMATIC_RESTART=PASS; DPAPI_JOURNAL=PASS'
    Write-Output 'IDENT_006C_REAL_SQL_LAB=PASS; SQL=LOCAL_SYNTHETIC_ONLY; EX_18_4=NONE; PRODUCTION=NONE'
}finally{
    if(Get-Service -Name AzIdent006C -ErrorAction SilentlyContinue){sc.exe config AzIdent006C start= disabled|Out-Null;Stop-Service AzIdent006C -ErrorAction SilentlyContinue}
    if($server -and -not $server.HasExited){Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue}
    if(Test-Path -LiteralPath (Join-Path $sqlLab 'sql-password.synthetic')){Remove-Item -LiteralPath (Join-Path $sqlLab 'sql-password.synthetic') -Force}
    $admin.Dispose()
}
