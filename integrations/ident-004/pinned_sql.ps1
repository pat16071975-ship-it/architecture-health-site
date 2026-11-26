# The approved single-file worker, pin and native extraction cache are provisioned
# separately. No downloading, installation, trust-store changes or auto-enrollment.
function Assert-PinnedInputFile([string]$Path, $Sid) {
    $info = [System.IO.FileInfo]::new($Path)
    if (-not $info.Exists -or ($info.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'TLS_PIN_INPUT_FILE'
    }
    Assert-PrivateAcl ($info.GetAccessControl()) $Sid $false
}

function Invoke-PinnedSqlReader(
    [string]$Outbox, $Sid, [string]$WorkerSha256, [string]$CertificateSha256,
    [string]$ServerName, [int]$Port, [string]$Database, [string]$SqlUser,
    [string]$DateFrom, [string]$DateToExclusive, [string]$QuerySha256, [bool]$CheckOnly
) {
    if ($WorkerSha256 -cnotmatch '^[0-9a-f]{64}$' -or $CertificateSha256 -cnotmatch '^[0-9a-f]{64}$') {
        throw 'TLS_PIN_DIGEST_REQUIRED'
    }
    $dir = Assert-PrivateOutbox (Join-Path $Outbox 'connection') $Sid
    $cache = Assert-PrivateOutbox (Join-Path $dir 'bundle-cache') $Sid
    $exe = Join-Path $dir 'IdentSqlReader.exe'
    $cert = Join-Path $dir 'server.cer'
    Assert-PinnedInputFile $exe $Sid
    Assert-PinnedInputFile $cert $Sid
    $lock = $null
    $process = $null
    $startedProcess = $false
    $digest = $null
    try {
        $lock = [System.IO.File]::Open($exe,[System.IO.FileMode]::Open,[System.IO.FileAccess]::Read,[System.IO.FileShare]::Read)
        $digest = [System.Security.Cryptography.SHA256]::Create()
        $actual = [System.BitConverter]::ToString($digest.ComputeHash($lock)).Replace('-','').ToLowerInvariant()
        if ($actual -cne $WorkerSha256) { throw 'TLS_PIN_WORKER_DIGEST_MISMATCH' }
        # The child independently verifies and locks the public certificate before login.
        # This argument contains configuration only, never the password.
        $config = [ordered]@{
            ServerName=$ServerName; Port=$Port; Database=$Database; SqlUser=$SqlUser
            CertificatePath=$cert; CertificateSha256=$CertificateSha256
            DateFrom=$DateFrom; DateToExclusive=$DateToExclusive; QuerySha256=$QuerySha256; CheckOnly=$CheckOnly
        }
        $args64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $config -Compress)))
        $psi = [System.Diagnostics.ProcessStartInfo]::new()
        $psi.FileName = $exe
        $psi.Arguments = $args64
        $psi.WorkingDirectory = $dir
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardInput = $false
        $psi.RedirectStandardError = $false
        $psi.CreateNoWindow = $false
        # No global environment changes. Keep self-extracted native libraries private.
        $psi.EnvironmentVariables['DOTNET_BUNDLE_EXTRACT_BASE_DIR'] = $cache
        foreach ($name in @('DOTNET_STARTUP_HOOKS','DOTNET_ADDITIONAL_DEPS','DOTNET_SHARED_STORE','CORECLR_ENABLE_PROFILING','CORECLR_PROFILER','CORECLR_PROFILER_PATH','COR_ENABLE_PROFILING','COR_PROFILER','COR_PROFILER_PATH')) {
            $psi.EnvironmentVariables.Remove($name)
        }
        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $psi
        if (-not $process.Start()) { throw 'TLS_PIN_WORKER_START' }
        $startedProcess = $true
        $read = $process.StandardOutput.ReadToEndAsync()
        if (-not $process.WaitForExit(300000)) { throw 'TLS_PIN_WORKER_TIMEOUT' }
        $output = $read.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($output) -or $output.Length -gt 65536) {
            throw 'TLS_PIN_WORKER_FAILED'
        }
        $result = ConvertFrom-Json -InputObject $output -ErrorAction Stop
        if ($result.query_sha256 -cne $QuerySha256) { throw 'TLS_PIN_QUERY_MISMATCH' }
        if ($result.check_only -isnot [bool] -or $result.check_only -ne $CheckOnly) { throw 'TLS_PIN_MODE_MISMATCH' }
        return $result
    } finally {
        if ($null -ne $process) {
            if ($startedProcess -and -not $process.HasExited) {
                $process.Kill()
                $process.WaitForExit()
            }
            $process.Dispose()
        }
        if ($null -ne $digest) { $digest.Dispose() }
        if ($null -ne $lock) { $lock.Dispose() }
    }
}
