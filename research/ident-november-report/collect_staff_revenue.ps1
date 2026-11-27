param(
    [Parameter(Mandatory=$true)][string]$ExpectedComputer,
    [Parameter(Mandatory=$true)][string]$Server,
    [Parameter(Mandatory=$true)][int]$Port,
    [Parameter(Mandatory=$true)][string]$Database,
    [Parameter(Mandatory=$true)][string]$SqlUser,
    [Parameter(Mandatory=$true)][string]$CertificateSha256
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
WITH limits AS (
    SELECT CAST('20261101' AS datetime2) AS month_start,
           CAST('20261127' AS datetime2) AS fact_end,
           CAST('20261201' AS datetime2) AS month_end
), service_rows AS (
    SELECT j.ID_Staffs AS staff_id,j.ID_Orders,j.Amount,j.AmountWithDiscount,
           o.ID AS order_found,o.IsBillFormed,o.ID_Receptions,
           CASE WHEN r.ReceptionCanceled IS NULL AND r.ID_ReceptionCancelReasons IS NULL
                  AND r.ReceptionStarted IS NOT NULL AND r.ReceptionEnded>r.ReceptionStarted
                  AND r.ReceptionStarted<l.fact_end AND r.ReceptionEnded>l.month_start
                THEN 1 ELSE 0 END AS completed_visit
    FROM dbo.RenderedServicesJournal j CROSS JOIN limits l
    LEFT JOIN dbo.Orders o ON o.ID=j.ID_Orders
    LEFT JOIN dbo.Receptions r ON r.ID=o.ID_Receptions
    WHERE j.DateTimeOrder>=l.month_start AND j.DateTimeOrder<l.fact_end
), active_staff AS (
    SELECT c.ID_Staffs AS staff_id FROM dbo.CurrentTimeTable c CROSS JOIN limits l
    WHERE c.DateOfWork>=l.month_start AND c.DateOfWork<l.month_end AND c.ID_Staffs IS NOT NULL AND c.IsWorkingTime=1 AND c.IsHeaderCell=0
    UNION
    SELECT r.ID_Staffs FROM dbo.Receptions r CROSS JOIN limits l
    WHERE r.PlanStart>=l.month_start AND r.PlanStart<l.month_end AND r.ReceptionCanceled IS NULL AND r.ID_ReceptionCancelReasons IS NULL
    UNION
    SELECT staff_id FROM service_rows
), staff_names AS (
    SELECT a.staff_id,
           LEFT(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(p.Surname))+N' '+LTRIM(RTRIM(p.Name))+N' '+LTRIM(RTRIM(p.Patronymic)),NCHAR(13),N' '),NCHAR(10),N' '),NCHAR(9),N' '),120) AS full_name,
           LEFT(REPLACE(REPLACE(REPLACE(ISNULL(np.Name,N''),NCHAR(13),N' '),NCHAR(10),N' '),NCHAR(9),N' '),120) AS position_name,
           LEFT(REPLACE(REPLACE(REPLACE(ISNULL(ns.Name,N''),NCHAR(13),N' '),NCHAR(10),N' '),NCHAR(9),N' '),120) AS specialty_name,
           s.Archive AS archived
    FROM active_staff a JOIN dbo.Staffs s ON s.ID_Persons=a.staff_id
    JOIN dbo.Persons p ON p.ID=s.ID_Persons
    LEFT JOIN dbo.NSI_Positions np ON np.UID=s.UID_NSI_Positions
    LEFT JOIN dbo.NSI_Specializations ns ON ns.UID=s.UID_NSI_Specializations
), financial AS (
    SELECT staff_id,
        SUM(CONVERT(decimal(28,6),ISNULL(AmountWithDiscount,0))) AS net_amount,
        SUM(CONVERT(decimal(28,6),ISNULL(Amount,0))) AS gross_amount,
        SUM(CONVERT(decimal(28,6),CASE WHEN completed_visit=1 THEN ISNULL(AmountWithDiscount,0) ELSE 0 END)) AS linked_amount,
        SUM(CONVERT(decimal(28,6),CASE WHEN IsBillFormed=0 THEN ISNULL(AmountWithDiscount,0) ELSE 0 END)) AS unformed_amount,
        SUM(CONVERT(decimal(28,6),CASE WHEN AmountWithDiscount<0 THEN AmountWithDiscount ELSE 0 END)) AS negative_amount,
        COUNT_BIG(*) AS service_rows_count,
        SUM(CONVERT(bigint,CASE WHEN AmountWithDiscount IS NULL THEN 1 ELSE 0 END)) AS null_amount_count,
        SUM(CONVERT(bigint,CASE WHEN order_found IS NULL THEN 1 ELSE 0 END)) AS missing_order_count,
        SUM(CONVERT(bigint,CASE WHEN completed_visit=1 THEN 1 ELSE 0 END)) AS linked_count
    FROM service_rows GROUP BY staff_id
), reserve_minutes AS (
    SELECT DISTINCT c.ID_Staffs AS staff_id,c.DateOfWork,c.ID_Times,rt.ID AS reserve_type_id,
           DATEDIFF(minute,t.TimeStartValue,t.TimeEndValue) AS minutes
    FROM dbo.CurrentTimeTable c CROSS JOIN limits l
    JOIN dbo.Times t ON t.ID=c.ID_Times
    JOIN dbo.TimeReserves tr ON tr.ID=c.ID_TimeReserves
    JOIN dbo.TimeReserveTypes rt ON rt.ID=tr.ID_TimeReserveTypes
    WHERE c.DateOfWork>=l.month_start AND c.DateOfWork<l.month_end
      AND c.ID_Staffs IS NOT NULL AND c.IsWorkingTime=1 AND c.IsHeaderCell=0
      AND t.TimeEndValue>t.TimeStartValue
), reserve_totals AS (
    SELECT staff_id,reserve_type_id,SUM(CONVERT(bigint,minutes)) AS minutes
    FROM reserve_minutes GROUP BY staff_id,reserve_type_id
), results AS (
    SELECT 'staff_name' AS metric,full_name AS staff,CONVERT(decimal(28,6),staff_id) AS value FROM staff_names
    UNION ALL SELECT 'staff_position',position_name,CONVERT(decimal(28,6),staff_id) FROM staff_names
    UNION ALL SELECT 'staff_specialty',specialty_name,CONVERT(decimal(28,6),staff_id) FROM staff_names
    UNION ALL SELECT 'staff_archived',CONVERT(nvarchar(120),staff_id),CONVERT(decimal(28,6),archived) FROM staff_names
    UNION ALL SELECT 'active_staff_id',CONVERT(nvarchar(120),staff_id),CONVERT(decimal(28,6),1) FROM active_staff
    UNION ALL SELECT 'revenue_net',CONVERT(nvarchar(120),staff_id),CONVERT(decimal(28,6),net_amount) FROM financial
    UNION ALL SELECT 'revenue_gross',CONVERT(nvarchar(120),staff_id),CONVERT(decimal(28,6),gross_amount) FROM financial
    UNION ALL SELECT 'revenue_linked_completed',CONVERT(nvarchar(120),staff_id),CONVERT(decimal(28,6),linked_amount) FROM financial
    UNION ALL SELECT 'revenue_unformed',CONVERT(nvarchar(120),staff_id),CONVERT(decimal(28,6),unformed_amount) FROM financial
    UNION ALL SELECT 'revenue_negative',CONVERT(nvarchar(120),staff_id),CONVERT(decimal(28,6),negative_amount) FROM financial
    UNION ALL SELECT 'service_rows_count',CONVERT(nvarchar(120),staff_id),CONVERT(decimal(28,6),service_rows_count) FROM financial
    UNION ALL SELECT 'null_amount_count',CONVERT(nvarchar(120),staff_id),CONVERT(decimal(28,6),null_amount_count) FROM financial
    UNION ALL SELECT 'missing_order_count',CONVERT(nvarchar(120),staff_id),CONVERT(decimal(28,6),missing_order_count) FROM financial
    UNION ALL SELECT 'linked_completed_count',CONVERT(nvarchar(120),staff_id),CONVERT(decimal(28,6),linked_count) FROM financial
    UNION ALL SELECT 'revenue_control_total',N'ALL',CONVERT(decimal(28,6),ISNULL(SUM(CONVERT(decimal(28,6),j.AmountWithDiscount)),0))
    FROM dbo.RenderedServicesJournal j CROSS JOIN limits l WHERE j.DateTimeOrder>=l.month_start AND j.DateTimeOrder<l.fact_end
    UNION ALL SELECT 'reserve_minutes',CONVERT(nvarchar(20),staff_id)+N':'+CONVERT(nvarchar(20),reserve_type_id),CONVERT(decimal(28,6),minutes) FROM reserve_totals
    UNION ALL SELECT 'reserve_type',LEFT(REPLACE(REPLACE(REPLACE(rt.Name,NCHAR(13),N' '),NCHAR(10),N' '),NCHAR(9),N' '),120),CONVERT(decimal(28,6),rt.ID)
    FROM dbo.TimeReserveTypes rt WHERE EXISTS(SELECT 1 FROM reserve_totals z WHERE z.reserve_type_id=rt.ID)
    UNION ALL SELECT 'staff_view_column',LEFT(c.name+N':'+ty.name,120),CONVERT(decimal(28,6),c.column_id)
    FROM sys.columns c JOIN sys.types ty ON ty.user_type_id=c.user_type_id WHERE c.object_id=OBJECT_ID(N'dbo.StaffsView')
)
SELECT metric,staff,value FROM results ORDER BY metric,staff
'@
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
        Write-Output 'IDENT_NOVEMBER_STAFF_REVENUE=PASS'
    }finally{$p.Dispose()}
}finally{
    $exeLock.Dispose()
    if($created){Remove-Item -LiteralPath $queryPath -Force}
}
