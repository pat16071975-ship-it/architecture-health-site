# This fixture is deliberately confined to a disposable hosted Windows runner.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or $env:GITHUB_REF -ne 'refs/heads/test/ident-004-tls-lab-7ffc6517') { throw 'LAB_RUNNER_REQUIRED' }
$lab = $env:IDENT_LAB_DIR
if (-not $lab -or -not (Test-Path -LiteralPath $lab)) { throw 'LAB_DIR_MISSING' }
$exe = Join-Path $env:RUNNER_TEMP 'ident-original-build\IdentSqlReader.exe'
if ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash -cne 'CD30E0FD3BD852B51C6F62CCB003F7DCF67DF61709F12251690EBEC7899C8C91') { throw 'WORKER_HASH' }
$py = (Get-Command python.exe -CommandType Application | Select-Object -First 1).Source
$harness = Join-Path $PSScriptRoot 'worker_harness.py'
$results = Join-Path $lab 'results'
New-Item -ItemType Directory -Path $results | Out-Null
# All credentials here are generated solely for this local synthetic SQL instance.
$password = 'Aa1!' + [Guid]::NewGuid().ToString('N')
Write-Host "::add-mask::$password"
$env:IDENT_LAB_PASSWORD = $password
$cs = 'Data Source=lpc:.\IDENTLAB;Initial Catalog=master;Integrated Security=True;Encrypt=False;TrustServerCertificate=True;Pooling=False;Connect Timeout=10'
$admin = New-Object System.Data.SqlClient.SqlConnection($cs)
$admin.Open()
function Exec-LabSql([string]$sql) {
    $cmd = $admin.CreateCommand()
    try { $cmd.CommandText = $sql; $cmd.CommandTimeout = 30; [void]$cmd.ExecuteNonQuery() } finally { $cmd.Dispose() }
}
function Scalar-LabSql([string]$sql) {
    $cmd = $admin.CreateCommand()
    try { $cmd.CommandText = $sql; $cmd.CommandTimeout = 30; return $cmd.ExecuteScalar() } finally { $cmd.Dispose() }
}
function LoginCount {
    $xml = Scalar-LabSql "SELECT CONVERT(nvarchar(max), t.target_data) FROM sys.dm_xe_session_targets t JOIN sys.dm_xe_sessions s ON s.address=t.event_session_address WHERE s.name=N'IdentTlsLabLogins' AND t.target_name=N'ring_buffer'"
    if (-not $xml) { throw 'LAB_XE_TARGET_MISSING' }
    $doc = [xml]$xml
    return @($doc.SelectNodes("//event[@name='login']/action[@name='client_app_name']/value") | Where-Object { $_.InnerText -eq 'IDENT-004 read-only pinned pilot' }).Count
}
function Start-Case([string]$name,[string]$certificate,[string]$hash,[bool]$checkOnly) {
    $out = Join-Path $results ($name + '.json')
    $args = @('"' + $harness + '"','worker','"' + $exe + '"','"' + $certificate + '"',$hash,'"' + $out + '"',("$checkOnly".ToLowerInvariant()))
    # Keep the original Process instance/handle so ExitCode is reliable even
    # when the child has already exited before Finish-Case is called.
    $psi = [Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $py
    $psi.Arguments = $args -join ' '
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $p = [Diagnostics.Process]::new()
    $p.StartInfo = $psi
    if (-not $p.Start()) { $p.Dispose(); throw 'LAB_HARNESS_START' }
    $null = $p.Handle
    $outTask = $p.StandardOutput.ReadToEndAsync()
    $errTask = $p.StandardError.ReadToEndAsync()
    return @{Process=$p; Path=$out; Name=$name; OutTask=$outTask; ErrTask=$errTask}
}
function Finish-Case($case) {
    $p = $case.Process
    try {
        if (-not $p.WaitForExit(125000)) { $p.Kill(); $p.WaitForExit(); throw ('LAB_CASE_TIMEOUT: '+$case.Name) }
        $p.WaitForExit()
        $harnessExit = $p.ExitCode
        $harnessOut = $case.OutTask.GetAwaiter().GetResult()
        $harnessErr = $case.ErrTask.GetAwaiter().GetResult()
        Write-Host ('HARNESS_CASE='+$case.Name+'; HARNESS_EXIT='+$harnessExit)
        if ($harnessOut) { Write-Host ('HARNESS_STDOUT='+$harnessOut.Replace($password,'[MASKED]')) }
        if ($harnessErr) { Write-Host ('HARNESS_STDERR='+$harnessErr.Replace($password,'[MASKED]')) }
        if (-not (Test-Path -LiteralPath $case.Path)) { throw ('LAB_CASE_NO_RESULT: '+$case.Name) }
        $r = Get-Content -LiteralPath $case.Path -Raw | ConvertFrom-Json
        Write-Host ('CASE '+$case.Name+' exit='+$r.exit_code+' prompt='+$r.prompt_seen+' stderr='+(([string]$r.stderr).Replace($password,'[MASKED]') -replace '[\r\n]+',' '))
        if ($harnessExit -ne 0 -or $null -ne $r.harness_error) {
            throw ('LAB_HARNESS_FAILED: '+$case.Name+' exit='+$harnessExit+' error='+([string]$r.harness_error).Replace($password,'[MASKED]'))
        }
        return $r
    } finally { $p.Dispose() }
}
function Assert-CheckSuccess($r) {
    if ($r.exit_code -ne 0 -or -not $r.prompt_seen) { throw 'PIN_ACCEPT_FAILED' }
    $j = $r.stdout | ConvertFrom-Json
    if ($j.check_only -ne $true -or $j.query_sha256 -ne '277cc67cb4c0369f7f3461ae5d8c50ac0443c3342cb44a81625cf701c52e443f' -or $j.payment_precision -ne 18 -or $j.payment_scale -ne 2) { throw 'CHECK_ONLY_RESPONSE' }
    if ($j.PSObject.Properties.Name -contains 'rows') { throw 'CHECK_ONLY_LEAK' }
}
function Assert-Rejected($r,[string]$reason,[bool]$prompt) {
    if ($r.exit_code -ne 1 -or $r.prompt_seen -ne $prompt -or $r.stdout.Length -ne 0 -or $r.stderr -notmatch ([regex]::Escape('REASON='+$reason))) { throw ('REJECTION_NOT_PROVEN: '+$reason) }
}
try {
    $version = Scalar-LabSql "SELECT CONVERT(nvarchar(128),SERVERPROPERTY('ProductVersion'))"
    $edition = Scalar-LabSql "SELECT CONVERT(nvarchar(128),SERVERPROPERTY('Edition'))"
    if ($edition -notmatch 'Express' -or $version -notmatch '^17\.') { throw 'LAB_SQL_ENGINE_IDENTITY' }
    Write-Host "SQL_ENGINE=PASS; VERSION=$version; EDITION=$edition"
    Exec-LabSql 'CREATE DATABASE IdentTlsLab'
    Exec-LabSql ("CREATE LOGIN ident_lab_reader WITH PASSWORD='"+$password+"', CHECK_POLICY=OFF, CHECK_EXPIRATION=OFF")
    Exec-LabSql @'
USE IdentTlsLab;
CREATE TABLE dbo.PaymentsIn (DatePayment date NOT NULL, ID_Patients int NOT NULL, Payment decimal(18,2) NOT NULL);
INSERT dbo.PaymentsIn VALUES ('2026-01-01',1,120.05),('2026-01-01',2,-10.05),('2026-01-02',3,25.00);
CREATE USER ident_lab_reader FOR LOGIN ident_lab_reader;
GRANT SELECT ON dbo.PaymentsIn TO ident_lab_reader;
'@
    Exec-LabSql @'
CREATE EVENT SESSION IdentTlsLabLogins ON SERVER ADD EVENT sqlserver.login(ACTION(sqlserver.client_app_name,sqlserver.server_principal_name,sqlserver.session_id)) ADD TARGET package0.ring_buffer WITH (MAX_DISPATCH_LATENCY=1 SECONDS);
ALTER EVENT SESSION IdentTlsLabLogins ON SERVER STATE=START;
'@
    # Observe fallback DER on the controlled local server before any login.
    $good = Join-Path $lab 'correct'
    $wrong = Join-Path $lab 'wrong'
    $broken = Join-Path $lab 'broken'
    $absent = Join-Path $lab 'absent'
    foreach ($d in @($good,$wrong,$broken,$absent)) { New-Item -ItemType Directory -Path $d | Out-Null }
    $goodCert = Join-Path $good 'server.cer'
    & $py $harness capture $goodCert
    if ($LASTEXITCODE -ne 0) { throw 'FALLBACK_CERT_CAPTURE' }
    $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($goodCert)
    try {
        Write-Host ('LAB_CERT_SUBJECT='+$cert.Subject+'; ISSUER='+$cert.Issuer)
        if ($cert.Subject -notmatch 'SSL_Self_Signed_Fallback' -or $cert.Subject -ne $cert.Issuer) { throw 'LAB_FALLBACK_CERT_NOT_CONFIRMED' }
        $chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
        try {
            $chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
            if ($chain.Build($cert)) { throw 'FALLBACK_UNEXPECTEDLY_TRUSTED' }
        } finally { $chain.Dispose() }
    } finally { $cert.Dispose() }
    $goodHash = (Get-FileHash -LiteralPath $goodCert -Algorithm SHA256).Hash.ToLowerInvariant()
    $other = New-SelfSignedCertificate -DnsName 'ident-lab-other.invalid' -CertStoreLocation 'Cert:\CurrentUser\My' -NotAfter (Get-Date).AddDays(7)
    $wrongCert = Join-Path $wrong 'server.cer'
    [IO.File]::WriteAllBytes($wrongCert,$other.RawData)
    $wrongHash = (Get-FileHash -LiteralPath $wrongCert -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($wrongHash -eq $goodHash) { throw 'WRONG_CERT_EQUAL' }
    $brokenCert = Join-Path $broken 'server.cer'
    [IO.File]::WriteAllBytes($brokenCert,[byte[]](1,2,3,4,5))
    $brokenHash = (Get-FileHash -LiteralPath $brokenCert -Algorithm SHA256).Hash.ToLowerInvariant()

    $r = Finish-Case (Start-Case 'correct_check_only' $goodCert $goodHash $true)
    Assert-CheckSuccess $r
    Start-Sleep -Seconds 3
    $before = LoginCount
    if ($before -lt 1) { throw 'POSITIVE_LOGIN_EVIDENCE_MISSING' }
    Write-Host 'TLS_CORRECT_PIN=PASS; CHECK_ONLY=PASS; NO_FINANCIAL_ROWS=YES'

    # A valid but different DER with its own correct SHA-256 must reach the TLS
    # handshake and fail, not merely fail the local file hash check.
    $r = Finish-Case (Start-Case 'wrong_certificate' $wrongCert $wrongHash $true)
    Assert-Rejected $r 'SQL_CONNECTION_OR_QUERY' $true
    if ($r.stderr -match 'NUMBER=18456\b') { throw 'WRONG_PIN_REACHED_AUTHENTICATION' }
    Start-Sleep -Seconds 3
    if ((LoginCount) -ne $before) { throw 'WRONG_PIN_AUTHENTICATED' }
    Write-Host 'TLS_WRONG_PIN=PASS; SUCCESSFUL_LOGIN=NO'

    $r = Finish-Case (Start-Case 'wrong_sha256' $goodCert ('0'*64) $true)
    Assert-Rejected $r 'PIN_SHA256_MISMATCH' $false
    Write-Host 'TLS_WRONG_HASH=PASS'
    $r = Finish-Case (Start-Case 'broken_der' $brokenCert $brokenHash $true)
    Assert-Rejected $r 'LOCAL_VALIDATION_OR_RUNTIME' $false
    Write-Host 'TLS_BROKEN_DER=PASS'
    $r = Finish-Case (Start-Case 'missing_der' (Join-Path $absent 'server.cer') $goodHash $true)
    Assert-Rejected $r 'LOCAL_VALIDATION_OR_RUNTIME' $false
    Write-Host 'TLS_MISSING_DER=PASS'

    # Lock only the synthetic table while a real worker session connects. This
    # gives time to inspect encrypt_option and auth_scheme in the server DMV.
    $locker = New-Object System.Data.SqlClient.SqlConnection('Data Source=lpc:.\IDENTLAB;Initial Catalog=IdentTlsLab;Integrated Security=True;Encrypt=False;TrustServerCertificate=True;Pooling=False')
    $locker.Open()
    $tx = $locker.BeginTransaction()
    $lockcmd = $locker.CreateCommand()
    $lockcmd.Transaction = $tx
    $lockcmd.CommandText = 'SELECT COUNT_BIG(*) FROM dbo.PaymentsIn WITH (TABLOCKX,HOLDLOCK)'
    [void]$lockcmd.ExecuteScalar()
    $case = $null
    try {
        $case = Start-Case 'correct_synthetic_financial' $goodCert $goodHash $false
        $deadline = (Get-Date).AddSeconds(18)
        $encrypted = $false
        do {
            $evidence = Scalar-LabSql "SELECT TOP (1) CONCAT(c.encrypt_option,':',c.auth_scheme,':',c.net_transport,':',c.client_net_address) FROM sys.dm_exec_connections c JOIN sys.dm_exec_sessions s ON s.session_id=c.session_id WHERE s.login_name=N'ident_lab_reader' AND s.program_name=N'IDENT-004 read-only pinned pilot'"
            if ($evidence) {
                if ($evidence -ne 'TRUE:SQL:TCP:127.0.0.1') { throw ('SQL_ENCRYPTION_EVIDENCE_INVALID: '+$evidence) }
                $encrypted = $true
                Write-Host ('SERVER_CONNECTION='+$evidence)
                break
            }
            if ($case.Process.HasExited) { break }
            Start-Sleep -Milliseconds 100
        } while ((Get-Date) -lt $deadline)
        if (-not $encrypted) { throw 'SERVER_ENCRYPTION_EVIDENCE_MISSING' }
    } finally { $tx.Rollback(); $lockcmd.Dispose(); $tx.Dispose(); $locker.Dispose() }
    $r = Finish-Case $case
    if ($r.exit_code -ne 0 -or -not $r.prompt_seen) { throw 'SYNTHETIC_QUERY_FAILED' }
    $j = $r.stdout | ConvertFrom-Json
    $rows = @($j.rows)
    if ($j.check_only -ne $false -or $rows.Count -ne 2 -or $rows[0].date -ne '2026-01-01' -or $rows[0].records_count -ne 2 -or $rows[0].paying_patients -ne 1 -or $rows[0].positive_amount -ne '120.05' -or $rows[0].negative_amount -ne '-10.05' -or $rows[0].net_amount -ne '110.00' -or $rows[1].date -ne '2026-01-02' -or $rows[1].net_amount -ne '25.00') { throw 'SYNTHETIC_RESULT_MISMATCH' }
    Write-Host 'TLS_ENCRYPTED_CONNECTION=PASS; SYNTHETIC_QUERY=PASS'
    # Verify the exact original binary still succeeds following rejected cases.
    $r = Finish-Case (Start-Case 'correct_after_rejections' $goodCert $goodHash $true)
    Assert-CheckSuccess $r
    Write-Host 'TLS_RECOVERY_AFTER_REJECTIONS=PASS'
    $summary = [ordered]@{
        source_head='7ffc65174a408a75afc237247045008e7bab51dd'; executable_sha256=(Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
        sql_engine=$version; edition=$edition; endpoint='127.0.0.1:15433'; correct_fallback_pin='PASS'; incorrect_valid_pin='PASS'; wrong_hash='PASS'; invalid_der='PASS'; missing_der='PASS'; check_only='PASS'; encrypted_connection='PASS'; synthetic_query='PASS'; recovery='PASS'
        clinic='NOT_CONNECTED'; production='UNCHANGED'; original_binary='UNCHANGED'
    }
    $summary | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $results 'summary.json') -Encoding UTF8
    Write-Host 'IDENT_TLS_LAB=PASS'
} finally {
    $env:IDENT_LAB_PASSWORD = $null
    $admin.Dispose()
}
