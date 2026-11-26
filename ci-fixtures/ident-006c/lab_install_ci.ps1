# Only the ephemeral GitHub-hosted Windows fixture. No production.
$ErrorActionPreference='Stop'
if($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or $env:GITHUB_REF -ne 'refs/heads/test/ident-006c-sender-20261126'){throw 'DISPOSABLE_RUNNER_REQUIRED'}
$root=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)) 'AZ-IDENT006C'
if(Test-Path -LiteralPath $root){throw 'LAB_ROOT_ALREADY_EXISTS'}
if(Get-Service -Name AzIdent006C -ErrorAction SilentlyContinue){throw 'LAB_SERVICE_ALREADY_EXISTS'}
$lab=Join-Path $env:RUNNER_TEMP 'ident006c-lab'
New-Item -ItemType Directory -Path $lab -ErrorAction Stop|Out-Null
$source=Split-Path -Parent $PSScriptRoot
$package=$env:IDENT_SENDER_PUBLISH
$tests=$env:IDENT_SENDER_TEST_EXE
$manifest=(Get-FileHash -LiteralPath (Join-Path $package 'package-manifest.json') -Algorithm SHA256).Hash.ToLowerInvariant()
$machineGuid=[string](Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid).MachineGuid
$hash=[Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($machineGuid))
$machine=[Convert]::ToHexString($hash).ToLowerInvariant()
$installer=Join-Path $source 'deploy/install.ps1'
$common=@('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',$installer,'-ExpectedComputerName',$env:COMPUTERNAME,'-ExpectedMachineGuidSha256',$machine,'-ManifestSha256',$manifest)
function Step([string[]]$Rest) {
    & powershell.exe @common @Rest
    if($LASTEXITCODE -ne 0){throw 'INSTALLER_STEP_FAILED'}
}
function Await([scriptblock]$Condition,[int]$Seconds,[string]$Reason) {
    $until=(Get-Date).AddSeconds($Seconds)
    do { if(& $Condition){return};Start-Sleep -Milliseconds 500 }while((Get-Date) -lt $until)
    throw $Reason
}
# CI-only preparation; exact audited installer and sender remain unchanged.
# SetFileSecurityW with DACL_SECURITY_INFORMATION does not propagate the
# directory's security descriptor to existing children. Do not use Set-Acl
# or SetNamedSecurityInfo on the drive root (the prior run timed out there).
if($env:RUNNER_ENVIRONMENT -ne 'github-hosted'){throw 'GITHUB_HOSTED_RUNNER_REQUIRED'}
$driveRoot=[IO.Path]::GetPathRoot($root)
if($driveRoot -ine ($env:SystemDrive+'\') -or $driveRoot -ine 'C:\'){throw 'EXPECTED_DISPOSABLE_SYSTEM_VOLUME'}
$labAllowed=@('S-1-5-32-544','S-1-5-18','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
$createMask=6
$external=Split-Path -Parent $root
$rootBefore=$null
$needsRestore=$false
$python=(Get-Command python.exe -CommandType Application|Select-Object -First 1).Source
$nativeScript=Join-Path $PSScriptRoot 'ci_root_dacl.py'
function Read-Descriptor([string]$Path){
    $acl=([IO.DirectoryInfo]::new($Path)).GetAccessControl([Security.AccessControl.AccessControlSections]::Access -bor [Security.AccessControl.AccessControlSections]::Owner -bor [Security.AccessControl.AccessControlSections]::Group)
    return [Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(),0)
}
function Descriptor-Bytes($Descriptor){
    $bytes=[byte[]]::new($Descriptor.BinaryLength)
    $Descriptor.GetBinaryForm($bytes,0)
    return ,$bytes
}
function Descriptor-Sddl($Descriptor){
    return $Descriptor.GetSddlForm([Security.AccessControl.AccessControlSections]::Access -bor [Security.AccessControl.AccessControlSections]::Owner -bor [Security.AccessControl.AccessControlSections]::Group)
}
function Invoke-RootDacl([string]$Path){
    Write-Output ('LAB_NATIVE_DACL_BEGIN='+[IO.Path]::GetFileName($Path))
    $proc=Start-Process -FilePath $python -ArgumentList @('-B',('"'+$nativeScript+'"'),('"'+$Path+'"')) -NoNewWindow -PassThru
    try{
        if(-not $proc.WaitForExit(30000)){$proc.Kill();throw 'LAB_NATIVE_DACL_TIMEOUT'}
        if($proc.ExitCode -ne 0){throw 'LAB_NATIVE_DACL_FAILED'}
    }finally{$proc.Dispose()}
    Write-Output 'LAB_NATIVE_DACL_END=PASS'
}
$childPaths=@($external,(Join-Path $driveRoot 'Windows'),(Join-Path $driveRoot 'Users'))
$childSnapshots=@{}
foreach($p in $childPaths){$childSnapshots[$p]=Descriptor-Sddl (Read-Descriptor $p)}
function Assert-ChildrenUnchanged {
    foreach($p in $childPaths){
        if((Descriptor-Sddl (Read-Descriptor $p)) -cne $childSnapshots[$p]){throw 'LAB_CHILD_DACL_CHANGED'}
    }
}
try{
    $rootBefore=Read-Descriptor $driveRoot
    $candidate=[Security.AccessControl.RawSecurityDescriptor]::new((Descriptor-Bytes $rootBefore),0)
    $removed=0
    # Only direct, non-inheritable create-only grants may be removed, only on
    # the disposable runner root. Any unexpected layout fails closed.
    for($i=$candidate.DiscretionaryAcl.Count-1;$i -ge 0;$i--){
        $r=$candidate.DiscretionaryAcl[$i]
        if($r -is [Security.AccessControl.CommonAce] -and $r.AceQualifier -eq 'AccessAllowed' -and
           $r.SecurityIdentifier.Value -notin $labAllowed -and ([int]$r.AceFlags -band [int][Security.AccessControl.AceFlags]::InheritOnly) -eq 0 -and
           ($r.AccessMask -band $createMask) -ne 0){
            if($r.IsCallback -or [int]$r.AceFlags -ne 0 -or ($r.AccessMask -band (-bnot $createMask)) -ne 0){throw 'LAB_ROOT_UNEXPECTED_ACE'}
            Write-Output ('LAB_ONLY_REMOVE_DIRECT_CREATE_ACE='+$r.SecurityIdentifier.Value+'; MASK='+$r.AccessMask)
            $candidate.DiscretionaryAcl.RemoveAce($i);$removed++
        }
    }
    if($removed -gt 0){
        $negativeArgs=@('-Mode','Plan','-PackageDirectory',$package)
        $negative=@(& powershell.exe @common @negativeArgs)
        $negativeExit=$LASTEXITCODE
        Write-Output ('LAB_NEGATIVE_PLAN_EXIT='+$negativeExit+'; OUTPUT='+($negative -join '|'))
        if($negativeExit -ne 1 -or 'STOP=ANCESTOR_ACL' -cnotin $negative){throw 'LIVE_PLAN_MISSED_PARENT_CREATE_RIGHT'}
        if((Test-Path -LiteralPath $root) -or (Get-Service -Name AzIdent006C -ErrorAction SilentlyContinue)){throw 'NEGATIVE_PLAN_MUTATED'}
        Write-Output 'LAB_DEFAULT_UNSAFE_PARENT_PLAN=REJECTED_WITHOUT_MUTATION'
        $backup=Join-Path $lab 'original-root-security.bin'
        $next=Join-Path $lab 'candidate-root-security.bin'
        [IO.File]::WriteAllBytes($backup,(Descriptor-Bytes $rootBefore))
        [IO.File]::WriteAllBytes($next,(Descriptor-Bytes $candidate))
        $needsRestore=$true
        Invoke-RootDacl $next
        if((Descriptor-Sddl (Read-Descriptor $driveRoot)) -cne (Descriptor-Sddl $candidate)){throw 'LAB_ROOT_DACL_MISMATCH'}
        Assert-ChildrenUnchanged
        Write-Output 'LAB_NONPROPAGATING_ROOT_DACL=PASS; CHILD_DACLS_UNCHANGED=YES'
    }else{Write-Output 'LAB_ROOT_ALREADY_SAFE=YES'}
Step @('-Mode','Plan','-PackageDirectory',$package)
if(Test-Path -LiteralPath $root){throw 'PLAN_MUTATED'}
Step @('-Mode','Stage','-PackageDirectory',$package,'-Confirm','IDENT-006C-STAGE')
$svc=Get-CimInstance Win32_Service -Filter "Name='AzIdent006C'"
if($svc.StartMode -ne 'Disabled' -or $svc.State -ne 'Stopped' -or $svc.StartName -ine 'NT SERVICE\AzIdent006C'){throw 'STAGE_BINDING'}
& powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'test_acl.ps1') -TestExe $tests
if($LASTEXITCODE -ne 0){throw 'IDENT_006C_01_ACL_TEST_FAILED'}
$openssl=(Get-Command openssl.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if(-not $openssl){$openssl='C:\Program Files\Git\usr\bin\openssl.exe'}
if(-not (Test-Path -LiteralPath $openssl)){throw 'LAB_OPENSSL'}
function OpenSsl([string[]]$Arguments){& $openssl @Arguments 2>$null;if($LASTEXITCODE -ne 0){throw 'LAB_CERTIFICATE_CREATION'}}
$ca=Join-Path $lab 'ca.pem';$caKey=Join-Path $lab 'ca.key';$leaf=Join-Path $lab 'leaf.pem';$leafKey=Join-Path $lab 'leaf.key';$csr=Join-Path $lab 'leaf.csr';$ext=Join-Path $lab 'leaf.ext'
OpenSsl @('req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=IDENT006C Disposable CA','-addext','basicConstraints=critical,CA:TRUE','-addext','keyUsage=critical,keyCertSign,cRLSign','-keyout',$caKey,'-out',$ca)
OpenSsl @('req','-newkey','rsa:2048','-nodes','-subj','/CN=ident-lab.test','-keyout',$leafKey,'-out',$csr)
[IO.File]::WriteAllLines($ext,@('basicConstraints=critical,CA:FALSE','keyUsage=critical,digitalSignature,keyEncipherment','extendedKeyUsage=serverAuth','subjectAltName=DNS:ident-lab.test'))
OpenSsl @('x509','-req','-in',$csr,'-CA',$ca,'-CAkey',$caKey,'-CAcreateserial','-days','1','-extfile',$ext,'-out',$leaf)
OpenSsl @('x509','-in',$ca,'-outform','DER','-out',(Join-Path $lab 'ca.cer'))
OpenSsl @('x509','-in',$leaf,'-outform','DER','-out',(Join-Path $lab 'fixture.cer'))
$trusted=Import-Certificate -FilePath (Join-Path $lab 'ca.cer') -CertStoreLocation Cert:\LocalMachine\Root
$hosts=Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$hostsBefore=[IO.File]::ReadAllBytes($hosts)
[IO.File]::AppendAllText($hosts,([Environment]::NewLine+'127.0.0.1 ident-lab.test'+[Environment]::NewLine),[Text.Encoding]::ASCII)
$server=$null
try {
    & $tests lab-seed $lab
    if($LASTEXITCODE -ne 0){throw 'SYNTHETIC_TEST_FAILED'}
    $exe=Join-Path $root 'app\AzIdentSender.exe'
    & $exe --check-local
    if($LASTEXITCODE -ne 0){throw 'INSTALLED_LOCAL_CHECK_FAILED'}
    $state=Join-Path $root 'state'
    $journal=Join-Path $state 'journal'
    if(@(Get-ChildItem -LiteralPath $journal -File -Filter 'packet-*').Count -ne 1){throw 'SEED_MISSING'}
    $python=(Get-Command python.exe -CommandType Application|Select-Object -First 1).Source
    $server=Start-Process -FilePath $python -ArgumentList @('-B',('"' + (Join-Path $PSScriptRoot 'lab_server.py') + '"'),('"' + $lab + '"')) -PassThru -NoNewWindow -RedirectStandardOutput (Join-Path $lab 'server.stdout') -RedirectStandardError (Join-Path $lab 'server.stderr')
    Await {Test-Path -LiteralPath (Join-Path $lab 'https-ready')} 30 'HTTPS_FIXTURE_START'
    Step @('-Mode','Enable','-Confirm','IDENT-006C-ENABLE')
    Await {Test-Path -LiteralPath (Join-Path $lab 'first-accepted')} 45 'FIRST_HTTPS_COMMIT_MISSING'
    $ack=Join-Path $journal 'ack-0000000000000001.bin'
    if(Test-Path -LiteralPath $ack){throw 'ACK_WAS_NOT_LOST'}
    $pidBefore=(Get-CimInstance Win32_Service -Filter "Name='AzIdent006C'").ProcessId
    if($pidBefore -le 0){throw 'SERVICE_PID'}
    Stop-Process -Id $pidBefore -Force
    Await {
        $s=Get-CimInstance Win32_Service -Filter "Name='AzIdent006C'"
        $s.State -eq 'Running' -and $s.ProcessId -gt 0 -and $s.ProcessId -ne $pidBefore
    } 100 'SCM_AUTOMATIC_RESTART_FAILED'
    Await {(Test-Path -LiteralPath $ack) -and (Test-Path -LiteralPath (Join-Path $lab 'exact-duplicate'))} 60 'DURABLE_ACK_MISSING'
    if(@(Get-ChildItem -LiteralPath $journal -File -Filter 'packet-*').Count -ne 1){throw 'DUPLICATE_LOCAL_PACKET'}
    Await {
        try {
            $status=Get-Content -LiteralPath (Join-Path $state 'status.json') -Raw|ConvertFrom-Json
            $status.acked_sequence -eq 1 -and $status.local_sequence -eq 1
        } catch {$false}
    } 15 'STATUS_SEQUENCE'
    Write-Output 'WINDOWS_SCM_SERVICE=PASS; VIRTUAL_ACCOUNT_DPAPI=PASS; HTTPS_VERIFIED=PASS; LOST_ACK=PASS; SCM_AUTOMATIC_RESTART=PASS; EXACT_RETRY=PASS'
    Step @('-Mode','Disable','-Confirm','IDENT-006C-DISABLE')
    if(-not (Test-Path -LiteralPath $ack)){throw 'DISABLE_LOST_DATA'}
    Write-Output 'IDENT_006C_SYNTHETIC_LAB=PASS; AUTOSTART_CONFIGURED=YES; REAL_SQL=NOT_RUN; PRODUCTION=NONE'
} finally {
    if(Get-Service -Name AzIdent006C -ErrorAction SilentlyContinue){
        sc.exe config AzIdent006C start= disabled|Out-Null
        Stop-Service AzIdent006C -ErrorAction SilentlyContinue
    }
    if($server -and -not $server.HasExited){Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue}
    [IO.File]::WriteAllBytes($hosts,$hostsBefore)
    if($trusted){Remove-Item -LiteralPath ('Cert:\LocalMachine\Root\'+$trusted.Thumbprint) -ErrorAction SilentlyContinue}
}
}finally{
    if($needsRestore){
        Invoke-RootDacl $backup
        if((Descriptor-Sddl (Read-Descriptor $driveRoot)) -cne (Descriptor-Sddl $rootBefore)){throw 'LAB_ROOT_DACL_RESTORE_FAILED'}
        Assert-ChildrenUnchanged
        Write-Output 'LAB_ORIGINAL_ROOT_DACL_RESTORED=PASS; CHILD_DACLS_UNCHANGED=YES'
    }
}
