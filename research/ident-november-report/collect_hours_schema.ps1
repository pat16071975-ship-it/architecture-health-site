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
), work_slots AS (
    SELECT DISTINCT c.ID_Staffs AS staff_id,c.DateOfWork,t.TimeStartValue,t.TimeEndValue
    FROM dbo.CurrentTimeTable c
    JOIN dbo.Times t ON t.ID=c.ID_Times
    CROSS JOIN limits l
    WHERE c.DateOfWork>=l.month_start AND c.DateOfWork<l.month_end
      AND c.ID_Staffs IS NOT NULL AND c.IsWorkingTime=1
      AND c.IsHeaderCell=0
), visit_rows AS (
    SELECT r.ID_Staffs AS staff_id,r.PlanStart,r.PlanEnd,r.ReceptionStarted,r.ReceptionEnded,
           r.PatientAppeared,r.ReceptionCanceled,r.ID_ReceptionCancelReasons
    FROM dbo.Receptions r CROSS JOIN limits l
    WHERE r.PlanStart>=l.month_start AND r.PlanStart<l.month_end
), intervals AS (
    SELECT 'schedule_seconds' AS metric,w.staff_id,
        DATEADD(second,DATEDIFF(second,CAST('00:00:00' AS time),w.TimeStartValue),CAST(w.DateOfWork AS datetime2)) AS s,
        DATEADD(second,DATEDIFF(second,CAST('00:00:00' AS time),w.TimeEndValue),CAST(w.DateOfWork AS datetime2)) AS e
    FROM work_slots w WHERE w.TimeEndValue>w.TimeStartValue
    UNION ALL
    SELECT 'actual_seconds',v.staff_id,
        CASE WHEN v.ReceptionStarted<l.month_start THEN l.month_start ELSE CAST(v.ReceptionStarted AS datetime2) END,
        CASE WHEN v.ReceptionEnded>l.fact_end THEN l.fact_end ELSE CAST(v.ReceptionEnded AS datetime2) END
    FROM visit_rows v CROSS JOIN limits l
    WHERE v.ReceptionCanceled IS NULL AND v.ID_ReceptionCancelReasons IS NULL
      AND v.ReceptionStarted IS NOT NULL AND v.ReceptionEnded IS NOT NULL
      AND v.ReceptionEnded>v.ReceptionStarted
      AND v.ReceptionStarted<l.fact_end AND v.ReceptionEnded>l.month_start
      AND v.PlanStart<l.fact_end
    UNION ALL
    SELECT 'booked_seconds',v.staff_id,CAST(v.PlanStart AS datetime2),
        CASE WHEN v.PlanEnd>l.month_end THEN l.month_end ELSE CAST(v.PlanEnd AS datetime2) END
    FROM visit_rows v CROSS JOIN limits l
    WHERE v.PlanStart>=l.fact_end AND v.PlanStart<l.month_end
      AND v.PlanEnd>v.PlanStart
      AND v.ReceptionCanceled IS NULL AND v.ID_ReceptionCancelReasons IS NULL
), ordered_intervals AS (
    SELECT metric,staff_id,s,e,
        MAX(e) OVER(PARTITION BY metric,staff_id ORDER BY s,e ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prev_end
    FROM intervals WHERE e>s
), islands AS (
    SELECT metric,staff_id,s,e,
        SUM(CASE WHEN prev_end IS NULL OR s>prev_end THEN 1 ELSE 0 END)
        OVER(PARTITION BY metric,staff_id ORDER BY s,e ROWS UNBOUNDED PRECEDING) AS grp
    FROM ordered_intervals
), merged AS (
    SELECT metric,staff_id,grp,MIN(s) AS s,MAX(e) AS e
    FROM islands GROUP BY metric,staff_id,grp
), slot_flags_rows AS (
    SELECT c.ID_Staffs AS staff_id,c.IsWorkingTime AS working_flag,c.IsHeaderCell AS header_flag,
        CASE WHEN c.ID_TimeReserves IS NULL THEN 0 ELSE 1 END AS reserve_flag
    FROM dbo.CurrentTimeTable c CROSS JOIN limits l
    WHERE c.DateOfWork>=l.month_start AND c.DateOfWork<l.month_end AND c.ID_Staffs IS NOT NULL
), slot_flags_grouped AS (
    SELECT staff_id,working_flag,header_flag,reserve_flag,COUNT_BIG(*) AS slot_count
    FROM slot_flags_rows GROUP BY staff_id,working_flag,header_flag,reserve_flag
), results AS (
    SELECT metric,CONVERT(nvarchar(120),staff_id) AS staff,
        SUM(CONVERT(bigint,DATEDIFF(second,s,e))) AS value
    FROM merged GROUP BY metric,staff_id
    UNION ALL
    SELECT metric+'_raw',CONVERT(nvarchar(120),staff_id),
        SUM(CONVERT(bigint,DATEDIFF(second,s,e)))
    FROM intervals WHERE e>s GROUP BY metric,staff_id
    UNION ALL
    SELECT 'past_completed_count',CONVERT(nvarchar(120),v.staff_id),COUNT_BIG(*)
    FROM visit_rows v CROSS JOIN limits l
    WHERE v.PlanStart<l.fact_end AND v.ReceptionCanceled IS NULL AND v.ID_ReceptionCancelReasons IS NULL
      AND v.ReceptionEnded>v.ReceptionStarted
    GROUP BY v.staff_id
    UNION ALL
    SELECT 'past_incomplete_count',CONVERT(nvarchar(120),v.staff_id),COUNT_BIG(*)
    FROM visit_rows v CROSS JOIN limits l
    WHERE v.PlanStart<l.fact_end AND v.ReceptionCanceled IS NULL AND v.ID_ReceptionCancelReasons IS NULL
      AND (v.ReceptionStarted IS NULL OR v.ReceptionEnded IS NULL OR v.ReceptionEnded<=v.ReceptionStarted)
    GROUP BY v.staff_id
    UNION ALL
    SELECT 'past_appeared_missing_duration',CONVERT(nvarchar(120),v.staff_id),COUNT_BIG(*)
    FROM visit_rows v CROSS JOIN limits l
    WHERE v.PlanStart<l.fact_end AND v.ReceptionCanceled IS NULL AND v.ID_ReceptionCancelReasons IS NULL
      AND v.PatientAppeared IS NOT NULL
      AND (v.ReceptionStarted IS NULL OR v.ReceptionEnded IS NULL OR v.ReceptionEnded<=v.ReceptionStarted)
    GROUP BY v.staff_id
    UNION ALL
    SELECT 'future_booked_count',CONVERT(nvarchar(120),v.staff_id),COUNT_BIG(*)
    FROM visit_rows v CROSS JOIN limits l
    WHERE v.PlanStart>=l.fact_end AND v.ReceptionCanceled IS NULL AND v.ID_ReceptionCancelReasons IS NULL
      AND v.PlanEnd>v.PlanStart
    GROUP BY v.staff_id
    UNION ALL
    SELECT 'slot_flags',CONVERT(nvarchar(20),staff_id)+':work='+CONVERT(nvarchar(1),working_flag)+':header='+CONVERT(nvarchar(1),header_flag)+':reserve='+CONVERT(nvarchar(1),reserve_flag),
        slot_count
    FROM slot_flags_grouped
    UNION ALL
    SELECT 'slot_bad_duration',CONVERT(nvarchar(120),staff_id),COUNT_BIG(*)
    FROM work_slots WHERE TimeEndValue<=TimeStartValue GROUP BY staff_id
    UNION ALL
    SELECT 'slot_length_count',CONVERT(nvarchar(120),DATEDIFF(second,TimeStartValue,TimeEndValue)),COUNT_BIG(*)
    FROM work_slots GROUP BY DATEDIFF(second,TimeStartValue,TimeEndValue)
    UNION ALL
    SELECT 'engine_version',CONVERT(nvarchar(120),SERVERPROPERTY('ProductVersion')),CONVERT(bigint,compatibility_level)
    FROM sys.databases WHERE database_id=DB_ID()
    UNION ALL
    SELECT 'column',o.name+'.'+c.name+':'+ty.name+CASE WHEN c.is_nullable=1 THEN ':null' ELSE ':notnull' END,
        CONVERT(bigint,c.column_id)
    FROM sys.objects o JOIN sys.columns c ON c.object_id=o.object_id
    JOIN sys.types ty ON ty.user_type_id=c.user_type_id
    WHERE o.schema_id=SCHEMA_ID('dbo') AND o.type IN ('U','V')
      AND (o.name IN ('Persons','Orders','OrderServiceRelation','Returns','Treatments','RenderedServicesJournal',
      'ServiceItems','ServiceFolders','NSI_Positions','NSI_Specializations','OwnCompanies','TimeReserves','TimeReserveTypes','ProfessionNames','TreatDoctors')
      OR (o.type='V' AND (o.name LIKE '%Rendered%Service%' OR o.name LIKE '%Order%Service%')))
    UNION ALL
    SELECT 'view',o.name,CONVERT(bigint,0)
    FROM sys.views o WHERE o.schema_id=SCHEMA_ID('dbo')
      AND (o.name LIKE '%Service%' OR o.name LIKE '%Order%' OR o.name LIKE '%Reception%' OR o.name LIKE '%TimeTable%' OR o.name LIKE '%Staff%' OR o.name LIKE '%Report%' OR o.name LIKE '%Revenue%')
    UNION ALL
    SELECT 'foreign_key',OBJECT_NAME(f.parent_object_id)+'.'+pc.name+'>'+OBJECT_NAME(f.referenced_object_id)+'.'+rc.name,
        CONVERT(bigint,f.constraint_column_id)
    FROM sys.foreign_key_columns f JOIN sys.columns pc ON pc.object_id=f.parent_object_id AND pc.column_id=f.parent_column_id
    JOIN sys.columns rc ON rc.object_id=f.referenced_object_id AND rc.column_id=f.referenced_column_id
    WHERE OBJECT_NAME(f.parent_object_id) IN ('Orders','OrderServiceRelation','Returns','Treatments','ServiceItems','ServiceFolders','TimeReserves','TreatDoctors')
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
        Write-Output 'IDENT_NOVEMBER_HOURS_SCHEMA=PASS'
    }finally{$p.Dispose()}
}finally{
    $exeLock.Dispose()
    if($created){Remove-Item -LiteralPath $queryPath -Force}
}
