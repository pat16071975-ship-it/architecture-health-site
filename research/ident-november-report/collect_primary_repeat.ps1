param(
    [Parameter(Mandatory=$true)][string]$ExpectedComputer,
    [Parameter(Mandatory=$true)][string]$Server,
    [Parameter(Mandatory=$true)][int]$Port,
    [Parameter(Mandatory=$true)][string]$Database,
    [Parameter(Mandatory=$true)][string]$SqlUser,
    [Parameter(Mandatory=$true)][string]$CertificateSha256,
    [Parameter(Mandatory=$true)][ValidateRange(1,2147483647)][int]$StaffId
)
$ErrorActionPreference='Stop'
if($env:COMPUTERNAME -ine $ExpectedComputer){throw 'WRONG_COMPUTER'}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess){throw 'WINDOWS_POWERSHELL_X64_REQUIRED'}
function Get-BytesSha256([byte[]]$Bytes){
    $sha=[Security.Cryptography.SHA256]::Create()
    try{return [BitConverter]::ToString($sha.ComputeHash($Bytes)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
}
$wc=New-Object Net.WebClient
try{
    $guard=$wc.DownloadData('https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/7ffc65174a408a75afc237247045008e7bab51dd/integrations/ident-004/outbox_guard.ps1')
}finally{$wc.Dispose()}
if((Get-BytesSha256 $guard) -cne '57c6d49419ca492d3a7f0b03afb7aad7dc631454aff36ea764df43af060c0243'){throw 'GUARD_HASH_MISMATCH'}
. ([scriptblock]::Create([Text.Encoding]::UTF8.GetString($guard)))
$sid=Get-PrivateOutboxIdentity
$dir=Assert-PrivateOutbox (Join-Path $env:LOCALAPPDATA 'IDENT-Bridge\outbox\connection') $sid
$cache=Assert-PrivateOutbox (Join-Path $dir 'bundle-cache') $sid
$cert=Join-Path $dir 'server.cer'
$exe=Join-Path $dir 'IdentManagementReport.exe'
foreach($f in @($cert,$exe)){
    $info=[IO.FileInfo]::new($f)
    if(-not $info.Exists -or ($info.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'INPUT_FILE_UNSAFE'}
    Assert-PrivateAcl ($info.GetAccessControl()) $sid $false
}
if((Get-FileHash -LiteralPath $cert -Algorithm SHA256).Hash -ine $CertificateSha256){throw 'CERTIFICATE_MISMATCH'}
$sql=@'
WITH clock AS (
    SELECT GETDATE() AS observed
), periods AS (
    SELECT CONVERT(nvarchar(20),'2026-10') AS period_key,CAST('20261001' AS datetime) AS date_from,CAST('20261101' AS datetime) AS date_to
    UNION ALL
    SELECT N'2026-11',CAST('20261101' AS datetime),CASE WHEN observed<CAST('20261201' AS datetime) THEN observed ELSE CAST('20261201' AS datetime) END FROM clock
    UNION ALL
    SELECT N'2026-11-26',CAST('20261101' AS datetime),CAST('20261127' AS datetime)
), target_staff AS (
    SELECT s.ID_Persons AS staff_id,
        LEFT(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(p.Surname))+N' '+LTRIM(RTRIM(p.Name))+N' '+LTRIM(RTRIM(p.Patronymic)),NCHAR(13),N' '),NCHAR(10),N' '),NCHAR(9),N' '),120) AS full_name
    FROM dbo.Staffs s JOIN dbo.Persons p ON p.ID=s.ID_Persons
    WHERE s.ID_Persons=__STAFF_ID__
), target_visits AS (
    SELECT p.period_key,r.ID AS reception_id,r.ID_Patients AS patient_id
    FROM periods p JOIN dbo.Receptions r ON r.PlanStart>=p.date_from AND r.PlanStart<p.date_to
    JOIN target_staff s ON s.staff_id=r.ID_Staffs
    WHERE r.ReceptionCanceled IS NULL AND r.ID_ReceptionCancelReasons IS NULL
        AND r.ReceptionStarted IS NOT NULL AND r.ReceptionEnded>r.ReceptionStarted
        AND r.ReceptionEnded<=p.date_to
), cohort AS (
    SELECT DISTINCT patient_id FROM target_visits
), ranked_history AS (
    SELECT r.ID AS reception_id,
        ROW_NUMBER() OVER(PARTITION BY r.ID_Patients,r.ID_Staffs ORDER BY r.ReceptionStarted,r.ID) AS doctor_visit_number,
        ROW_NUMBER() OVER(PARTITION BY r.ID_Patients ORDER BY r.ReceptionStarted,r.ID) AS clinic_visit_number
    FROM dbo.Receptions r JOIN cohort c ON c.patient_id=r.ID_Patients
    CROSS JOIN clock x
    WHERE r.ReceptionCanceled IS NULL AND r.ID_ReceptionCancelReasons IS NULL
        AND r.ReceptionStarted IS NOT NULL AND r.ReceptionEnded>r.ReceptionStarted
        AND r.ReceptionEnded<=x.observed
), rendered AS (
    SELECT p.period_key,j.ID_Orders,o.ID_Receptions AS reception_id,o.ID AS order_found,
        j.AmountWithDiscount
    FROM periods p JOIN dbo.RenderedServicesJournal j ON j.DateTimeOrder>=p.date_from AND j.DateTimeOrder<p.date_to
    JOIN target_staff s ON s.staff_id=j.ID_Staffs
    LEFT JOIN dbo.Orders o ON o.ID=j.ID_Orders
), rendered_totals AS (
    SELECT period_key,
        SUM(CONVERT(decimal(28,6),ISNULL(AmountWithDiscount,0))) AS revenue,
        COUNT_BIG(*) AS row_count,
        SUM(CONVERT(bigint,CASE WHEN AmountWithDiscount IS NULL THEN 1 ELSE 0 END)) AS null_count,
        SUM(CONVERT(bigint,CASE WHEN order_found IS NULL THEN 1 ELSE 0 END)) AS missing_order_count
    FROM rendered GROUP BY period_key
), by_reception AS (
    SELECT period_key,reception_id,SUM(CONVERT(decimal(28,6),ISNULL(AmountWithDiscount,0))) AS revenue
    FROM rendered WHERE reception_id IS NOT NULL GROUP BY period_key,reception_id
), labeled AS (
    SELECT v.period_key,v.reception_id,v.patient_id,h.doctor_visit_number,h.clinic_visit_number,
        ISNULL(a.revenue,CONVERT(decimal(38,6),0)) AS revenue
    FROM target_visits v JOIN ranked_history h ON h.reception_id=v.reception_id
    LEFT JOIN by_reception a ON a.period_key=v.period_key AND a.reception_id=v.reception_id
), split_visits AS (
    SELECT v.period_key,v.patient_id,v.revenue,k.category
    FROM labeled v CROSS APPLY (
        VALUES (CASE WHEN v.doctor_visit_number=1 THEN N'doctor_primary' ELSE N'doctor_repeat' END),
               (CASE WHEN v.clinic_visit_number=1 THEN N'clinic_primary' ELSE N'clinic_repeat' END)
    ) k(category)
), categories AS (
    SELECT N'doctor_primary' AS category
    UNION ALL SELECT N'doctor_repeat'
    UNION ALL SELECT N'clinic_primary'
    UNION ALL SELECT N'clinic_repeat'
), split_totals AS (
    SELECT period_key,category,COUNT_BIG(*) AS visits,COUNT_BIG(DISTINCT patient_id) AS patients,
        SUM(CONVERT(decimal(28,6),revenue)) AS revenue
    FROM split_visits GROUP BY period_key,category
), visit_totals AS (
    SELECT period_key,COUNT_BIG(*) AS visits,COUNT_BIG(DISTINCT patient_id) AS patients,
        SUM(CONVERT(decimal(28,6),revenue)) AS linked_revenue
    FROM labeled GROUP BY period_key
), target_counts AS (
    SELECT period_key,COUNT_BIG(*) AS visits FROM target_visits GROUP BY period_key
), results AS (
    SELECT 'staff_name' AS metric,full_name AS staff,CONVERT(decimal(28,6),staff_id) AS value FROM target_staff
    UNION ALL SELECT 'server_local_time',CONVERT(nvarchar(120),observed,126),CONVERT(decimal(28,6),0) FROM clock
    UNION ALL SELECT 'period_end',p.period_key+N':'+CONVERT(nvarchar(40),p.date_to,126),CONVERT(decimal(28,6),0) FROM periods p
    UNION ALL SELECT 'visits',p.period_key+N':'+c.category,CONVERT(decimal(28,6),ISNULL(g.visits,0))
        FROM periods p CROSS JOIN categories c LEFT JOIN split_totals g ON g.period_key=p.period_key AND g.category=c.category
    UNION ALL SELECT 'patients',p.period_key+N':'+c.category,CONVERT(decimal(28,6),ISNULL(g.patients,0))
        FROM periods p CROSS JOIN categories c LEFT JOIN split_totals g ON g.period_key=p.period_key AND g.category=c.category
    UNION ALL SELECT 'revenue',p.period_key+N':'+c.category,CONVERT(decimal(28,6),ISNULL(g.revenue,0))
        FROM periods p CROSS JOIN categories c LEFT JOIN split_totals g ON g.period_key=p.period_key AND g.category=c.category
    UNION ALL SELECT 'total_visits',p.period_key,CONVERT(decimal(28,6),ISNULL(v.visits,0))
        FROM periods p LEFT JOIN visit_totals v ON v.period_key=p.period_key
    UNION ALL SELECT 'total_unique_patients',p.period_key,CONVERT(decimal(28,6),ISNULL(v.patients,0))
        FROM periods p LEFT JOIN visit_totals v ON v.period_key=p.period_key
    UNION ALL SELECT 'unclassified_visits',p.period_key,CONVERT(decimal(28,6),ISNULL(t.visits,0)-ISNULL(v.visits,0))
        FROM periods p LEFT JOIN target_counts t ON t.period_key=p.period_key LEFT JOIN visit_totals v ON v.period_key=p.period_key
    UNION ALL SELECT 'revenue_total',p.period_key,CONVERT(decimal(28,6),ISNULL(f.revenue,0))
        FROM periods p LEFT JOIN rendered_totals f ON f.period_key=p.period_key
    UNION ALL SELECT 'revenue_linked',p.period_key,CONVERT(decimal(28,6),ISNULL(v.linked_revenue,0))
        FROM periods p LEFT JOIN visit_totals v ON v.period_key=p.period_key
    UNION ALL SELECT 'revenue_unallocated',p.period_key,CONVERT(decimal(28,6),CONVERT(decimal(28,6),ISNULL(f.revenue,0))-CONVERT(decimal(28,6),ISNULL(v.linked_revenue,0)))
        FROM periods p LEFT JOIN rendered_totals f ON f.period_key=p.period_key LEFT JOIN visit_totals v ON v.period_key=p.period_key
    UNION ALL SELECT 'null_amount_count',p.period_key,CONVERT(decimal(28,6),ISNULL(f.null_count,0))
        FROM periods p LEFT JOIN rendered_totals f ON f.period_key=p.period_key
    UNION ALL SELECT 'missing_order_count',p.period_key,CONVERT(decimal(28,6),ISNULL(f.missing_order_count,0))
        FROM periods p LEFT JOIN rendered_totals f ON f.period_key=p.period_key
)
SELECT metric,staff,value FROM results ORDER BY staff,metric
'@
$sql=$sql.Replace('__STAFF_ID__',$StaffId.ToString([Globalization.CultureInfo]::InvariantCulture))
$bytes=[Text.UTF8Encoding]::new($false).GetBytes($sql.Replace("`r`n","`n"))
$querySha=Get-BytesSha256 $bytes
$queryPath=Join-Path $dir ('research-'+[guid]::NewGuid().ToString('N')+'.sql')
$exeLock=[IO.File]::Open($exe,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
$created=$false
try{
    $sha=[Security.Cryptography.SHA256]::Create()
    try{$actual=[BitConverter]::ToString($sha.ComputeHash($exeLock)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
    if($actual -cne 'e12575f0ba68a4352acaa36eb4c82591ac237e40607a7e7c057a9e69bff61f98'){throw 'EXE_HASH_MISMATCH'}
    $out=[IO.FileStream]::new($queryPath,[IO.FileMode]::CreateNew,[Security.AccessControl.FileSystemRights]::FullControl,[IO.FileShare]::None,65536,[IO.FileOptions]::None,(New-PrivateFileSecurity $sid))
    $created=$true
    try{$out.Write($bytes,0,$bytes.Length);$out.Flush($true)}finally{$out.Dispose()}
    Assert-PrivateAcl ([IO.FileInfo]::new($queryPath).GetAccessControl()) $sid $false
    $cfg=@{ServerName=$Server;Port=$Port;Database=$Database;SqlUser=$SqlUser;CertificatePath=$cert;CertificateSha256=$CertificateSha256;Mode='aggregate';QueryPath=$queryPath;QuerySha256=$querySha}
    $arg=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((ConvertTo-Json $cfg -Compress)))
    $psi=[Diagnostics.ProcessStartInfo]::new()
    $psi.FileName=$exe
    $psi.Arguments=$arg
    $psi.WorkingDirectory=$dir
    $psi.UseShellExecute=$false
    $psi.RedirectStandardOutput=$true
    $psi.StandardOutputEncoding=[Text.Encoding]::UTF8
    $psi.EnvironmentVariables['DOTNET_BUNDLE_EXTRACT_BASE_DIR']=$cache
    foreach($name in @('DOTNET_STARTUP_HOOKS','DOTNET_ADDITIONAL_DEPS','DOTNET_SHARED_STORE','CORECLR_ENABLE_PROFILING','CORECLR_PROFILER','CORECLR_PROFILER_PATH','COR_ENABLE_PROFILING','COR_PROFILER','COR_PROFILER_PATH')){
        $psi.EnvironmentVariables.Remove($name)
    }
    $p=[Diagnostics.Process]::Start($psi)
    try{
        $read=$p.StandardOutput.ReadToEndAsync()
        if(-not $p.WaitForExit(180000)){$p.Kill();$p.WaitForExit();throw 'REPORT_TIMEOUT'}
        if($p.ExitCode -ne 0){throw 'REPORT_READER_FAILED'}
        $data=ConvertFrom-Json ($read.GetAwaiter().GetResult())
        if($data.mode -cne 'aggregate' -or $data.tls -cne 'PINNED' -or $data.read_only -cne 'PASS' -or $data.query_sha256 -cne $querySha){throw 'RESULT_MISMATCH'}
        Write-Output "TLS=PINNED; READ_ONLY=PASS; OBSERVED=$($data.observed_at); QUERY_SHA256=$querySha"
        foreach($row in $data.rows){Write-Output ($row -join ' | ')}
        Write-Output 'IDENT_PRIMARY_REPEAT=PASS'
    }finally{$p.Dispose()}
}finally{
    $exeLock.Dispose()
    if($created){Remove-Item -LiteralPath $queryPath -Force}
}
