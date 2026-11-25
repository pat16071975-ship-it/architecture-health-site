# IDENT-004 candidate: one manual collection, no installer or scheduled task.
# Windows PowerShell 5.1 / System.Data.SqlClient. Requires trusted SQL TLS.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$ServerName,
    [Parameter(Mandatory=$true)][ValidateRange(1,65535)][int]$Port,
    [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$Database,
    [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$SqlUser,
    [Parameter(Mandatory=$true)][guid]$SourceId,
    [Parameter(Mandatory=$true)][guid]$StreamId,
    [Parameter(Mandatory=$true)][ValidateRange(1,9007199254740991)][long]$Sequence,
    [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$SourceTimezone,
    [Parameter(Mandatory=$true)][string]$DateFrom,
    [Parameter(Mandatory=$true)][string]$DateToExclusive,
    [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$PrivateOutbox,
    # This prototype reads totals for the whole source database, not a unit.
    [Parameter(Mandatory=$true)][switch]$ConfirmWholeDatabaseScope
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$invariant = [System.Globalization.CultureInfo]::InvariantCulture
$utf8 = [System.Text.UTF8Encoding]::new($false, $true)
$pw = $null
$conn = $null
$reader = $null
$cmd = $null
$tmp = $null
$hash = $null
try {
    if (-not $ConfirmWholeDatabaseScope) { throw 'WHOLE_DATABASE_SCOPE_NOT_APPROVED' }
    if ($SourceId -eq [guid]::Empty -or $StreamId -eq [guid]::Empty) { throw 'INVALID_SOURCE_ID' }
    if ($ServerName -notmatch '^[A-Za-z0-9][A-Za-z0-9.\-]*$') { throw 'INVALID_SERVER_NAME' }
    $start = [datetime]::ParseExact($DateFrom,'yyyy-MM-dd',$invariant)
    $end = [datetime]::ParseExact($DateToExclusive,'yyyy-MM-dd',$invariant)
    $days = ($end - $start).Days
    if ($days -lt 1 -or $days -gt 31) { throw 'INVALID_DATE_WINDOW' }
    if (-not [System.IO.Directory]::Exists($PrivateOutbox)) { throw 'PRIVATE_OUTBOX_MUST_ALREADY_EXIST' }
    $outbox = [System.IO.Path]::GetFullPath($PrivateOutbox)
    $sqlPath = Join-Path $PSScriptRoot 'sql/payments_daily_v1.sql'
    $sql = [System.IO.File]::ReadAllText($sqlPath,$utf8).Replace("`r`n","`n").Replace("`r","`n")
    $schemaSql = [System.IO.File]::ReadAllText((Join-Path $PSScriptRoot 'sql/payments_daily_v1_schema.sql'),$utf8)
    $hash = [System.Security.Cryptography.SHA256]::Create()
    $querySha = [System.BitConverter]::ToString($hash.ComputeHash($utf8.GetBytes($sql))).Replace('-','').ToLowerInvariant()
    $builder = [System.Data.SqlClient.SqlConnectionStringBuilder]::new()
    $builder.DataSource = "tcp:$ServerName,$Port"
    $builder.InitialCatalog = $Database
    $builder.Encrypt = $true
    $builder.TrustServerCertificate = $false
    $builder.ConnectTimeout = 10
    $builder.Pooling = $false
    $builder.ApplicationName = 'IDENT-004 read-only pilot'
    $pw = Read-Host 'SQL password' -AsSecureString
    $pw.MakeReadOnly()
    $credential = [System.Data.SqlClient.SqlCredential]::new($SqlUser,$pw)
    $conn = [System.Data.SqlClient.SqlConnection]::new($builder.ConnectionString,$credential)
    $conn.Open()
    $cmd = $conn.CreateCommand()
    $cmd.CommandTimeout = 30
    $cmd.CommandText = "SELECT DB_NAME(), HAS_PERMS_BY_NAME(N'dbo.PaymentsIn',N'OBJECT',N'SELECT')"
    $reader = $cmd.ExecuteReader()
    if (-not $reader.Read() -or $reader.GetString(0) -cne $Database -or $reader.IsDBNull(1) -or $reader.GetInt32(1) -ne 1) { throw 'SOURCE_PREFLIGHT_FAILED' }
    $reader.Dispose(); $reader = $null
    $cmd.CommandText = $schemaSql
    $reader = $cmd.ExecuteReader()
    $expected = @{DatePayment='date'; ID_Patients='int'; Payment='decimal'}
    $seen = @{}
    $precision = 0; $scale = 0
    while ($reader.Read()) {
        $column = $reader.GetString(0)
        $type = $reader.GetString(1)
        if (-not $expected.ContainsKey($column) -or $seen.ContainsKey($column) -or $type -cne $expected[$column] -or $reader.GetBoolean(2)) { throw 'INCOMPATIBLE_SOURCE_SCHEMA' }
        $seen[$column] = $true
        if ($column -ceq 'Payment') { $precision = [int]$reader.GetByte(3); $scale = [int]$reader.GetByte(4) }
    }
    if ($seen.Count -ne $expected.Count -or $precision -lt 1 -or $precision -gt 38 -or $scale -gt $precision) { throw 'INCOMPATIBLE_SOURCE_SCHEMA' }
    $reader.Dispose(); $reader = $null
    $cmd.CommandText = $sql
    [void]$cmd.Parameters.Add('@start_date',[System.Data.SqlDbType]::Date)
    [void]$cmd.Parameters.Add('@end_date_exclusive',[System.Data.SqlDbType]::Date)
    $cmd.Parameters['@start_date'].Value = $start
    $cmd.Parameters['@end_date_exclusive'].Value = $end
    $recordsByDate = @{}
    $started = [datetime]::UtcNow
    $reader = $cmd.ExecuteReader()
    while ($reader.Read()) {
        $date = $reader.GetString(0)
        $day = [datetime]::ParseExact($date,'yyyy-MM-dd',$invariant)
        if ($day -lt $start -or $day -ge $end -or $recordsByDate.ContainsKey($date)) { throw 'INVALID_SQL_RESULT' }
        $recordsByDate[$date] = [ordered]@{
            date = $date; records_count = $reader.GetInt64(1); paying_patients = $reader.GetInt64(2)
            positive_amount = $reader.GetString(3); negative_amount = $reader.GetString(4); net_amount = $reader.GetString(5)
        }
    }
    # No zero-filled package on query failure or an incomplete result set.
    if ($reader.NextResult()) { throw 'UNEXPECTED_EXTRA_RESULT_SET' }
    $reader.Dispose(); $reader = $null
    $finished = [datetime]::UtcNow
    $rows = @(
        for ($day = $start; $day -lt $end; $day = $day.AddDays(1)) {
            $date = $day.ToString('yyyy-MM-dd',$invariant)
            if ($recordsByDate.ContainsKey($date)) { $recordsByDate[$date] }
            else { [ordered]@{date=$date;records_count=0;paying_patients=0;positive_amount='0';negative_amount='0';net_amount='0'} }
        }
    )
    $batchId = [guid]::NewGuid().ToString('D')
    $payload = [ordered]@{
        contract_version=1; dataset='payments_daily_v1'; scope='whole_source_database'
        source_id=$SourceId.ToString('D'); stream_id=$StreamId.ToString('D'); batch_id=$batchId; sequence=$Sequence
        source_timezone=$SourceTimezone; date_from=$DateFrom; date_to_exclusive=$DateToExclusive
        query_started_at=$started.ToString("yyyy-MM-ddTHH:mm:ss.ffffff'Z'",$invariant)
        observed_at=$finished.ToString("yyyy-MM-ddTHH:mm:ss.ffffff'Z'",$invariant)
        coverage='full_query_window_as_observed'; query_sha256=$querySha
        schema=[ordered]@{payment_precision=$precision;payment_scale=$scale}; rows=$rows
    }
    $json = ConvertTo-Json -InputObject $payload -Depth 8 -Compress
    $bytes = $utf8.GetBytes($json)
    $tmp = Join-Path $outbox ('.' + $batchId + '.tmp')
    $target = Join-Path $outbox ($batchId + '.json')
    $file = [System.IO.File]::Open($tmp,[System.IO.FileMode]::CreateNew,[System.IO.FileAccess]::Write,[System.IO.FileShare]::None)
    try { $file.Write($bytes,0,$bytes.Length); $file.Flush($true) } finally { $file.Dispose() }
    [System.IO.File]::Move($tmp,$target)
    $tmp = $null
    Write-Output ('COLLECTION=PASS; DATASET=payments_daily_v1; DAYS=' + $days + '; BATCH=' + $batchId)
} catch {
    # Avoid logging connection strings, credentials, and raw SQL driver messages.
    Write-Error ('COLLECTION=STOP; TYPE=' + $_.Exception.GetType().Name + '; HResult=' + $_.Exception.HResult) -ErrorAction Continue
    exit 1
} finally {
    if ($null -ne $reader) { $reader.Dispose() }
    if ($null -ne $cmd) { $cmd.Dispose() }
    if ($null -ne $conn) { $conn.Dispose() }
    if ($null -ne $pw) { $pw.Dispose() }
    if ($null -ne $hash) { $hash.Dispose() }
    if ($null -ne $tmp -and [System.IO.File]::Exists($tmp)) { [System.IO.File]::Delete($tmp) }
}
