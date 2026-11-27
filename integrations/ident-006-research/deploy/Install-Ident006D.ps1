# SOURCE ONLY. Never apply without separate OWNER and independent audit authorization.
# IDENT DB is not accessed or changed by any operation here.
[CmdletBinding()]
param(
 [ValidateSet('Preflight','CIInspect','Apply','Verify','Rollback')]
 [string]$Mode='Preflight',
 [string]$Package,
 [string]$ExpectedPackageSha256,
 [string]$ApprovalPhrase
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$serviceName='AzIdentResearch'
$baseline='e0bc255f57aeb4346a14b749f27d8dcbab985b27'
$baselineTree='78261cfca54cd0ae43539ec5d8accb7c0287eaad'
$root=Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'AZ-IDENT-RESEARCH'
$app=Join-Path $root 'app'
$state=Join-Path $root 'state'
$marker=Join-Path $root 'source-only-installation.json'
$adminSid=[Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$systemSid=[Security.Principal.SecurityIdentifier]::new('S-1-5-18')

function Assert-Host([bool]$adminRequired) {
 if ($env:COMPUTERNAME -cne 'EX-18-4') { throw 'WRONG_COMPUTER' }
 if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitOperatingSystem) {throw 'WINDOWS_X64_REQUIRED'}
 if (([IO.DriveInfo]::new([IO.Path]::GetPathRoot($root))).DriveFormat -cne 'NTFS') {throw 'NTFS_REQUIRED'}
 if ($adminRequired) {
  $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
  if (-not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {throw 'ADMIN_REQUIRED'}
 }
}

function Inspect-Package([string]$archivePath,[string]$sha) {
 if ($sha -cnotmatch '^[a-f0-9]{64}$') {throw 'EXPECTED_SHA256_REQUIRED'}
 if (-not [IO.Path]::IsPathFullyQualified($archivePath)) {throw 'ABSOLUTE_PACKAGE_PATH_REQUIRED'}
 $item=Get-Item -LiteralPath $archivePath -ErrorAction Stop
 if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {throw 'PACKAGE_REPARSE_POINT'}
 if ($item.Length -lt 1000 -or $item.Length -gt 700MB) {throw 'PACKAGE_SIZE_INVALID'}
 if ((Get-FileHash $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant() -cne $sha) {throw 'PACKAGE_SHA256_MISMATCH'}
 $temp=Join-Path $env:TEMP ('ident006d-package-'+[Guid]::NewGuid().ToString('N'))
 New-Item -ItemType Directory -Path $temp | Out-Null
 try {
  Add-Type -AssemblyName System.IO.Compression
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $zip=[IO.Compression.ZipFile]::OpenRead($item.FullName)
  try {
   $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
   [long]$total=0
   foreach($entry in $zip.Entries) {
    $name=$entry.FullName.Replace('\','/')
    if ($name.StartsWith('/') -or $name.Contains('..') -or $name.Contains(':') -or $name -match '[\x00-\x1f]' -or
      $name -notmatch '^(payload|tools|manifest\.json)(/|$)' -or -not $seen.Add($name)) {throw 'UNSAFE_ZIP_ENTRY'}
    $total += $entry.Length
    if ($entry.Length -gt 250MB -or $total -gt 650MB) {throw 'ZIP_EXPANSION_LIMIT'}
   }
  } finally {$zip.Dispose()}
  [IO.Compression.ZipFile]::ExtractToDirectory($item.FullName,$temp)
  $manifestPath=Join-Path $temp 'manifest.json'
  $manifest=Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
  if ($manifest.source_commit -cne $baseline -or $manifest.source_tree -cne $baselineTree -or
      $manifest.platform -cne 'win-x64' -or $manifest.mode -cne 'SOURCE_ONLY_CANDIDATE' -or
      $manifest.installation -cne 'NOT_EXECUTED' -or $manifest.real_ident -cne 'NONE') {throw 'MANIFEST_BASELINE'}
  $files=@($manifest.files)
  if ($files.Count -lt 3 -or $files.Count -gt 1500) {throw 'MANIFEST_COUNT'}
  $seenFiles=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
  foreach($file in $files) {
   $rel=[string]$file.path
   if ($rel -cnotmatch '^(payload|tools)/[a-zA-Z0-9._/-]+$' -or $rel.Contains('..') -or -not $seenFiles.Add($rel)) {throw 'MANIFEST_PATH'}
   $path=Join-Path $temp ($rel.Replace('/',[IO.Path]::DirectorySeparatorChar))
   $found=Get-Item -LiteralPath $path -ErrorAction Stop
   if ($found.PSIsContainer -or ($found.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $found.Length -ne [long]$file.length) {throw 'MANIFEST_SIZE'}
   if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne [string]$file.sha256) {throw 'MANIFEST_SHA256'}
  }
  $actual=@(Get-ChildItem -LiteralPath $temp -Recurse -File | Where-Object {$_.FullName -cne $manifestPath})
  if ($actual.Count -ne $files.Count) {throw 'UNLISTED_FILE'}
  foreach($file in $actual) {
   $rel=$file.FullName.Substring($temp.Length+1).Replace('\','/')
   if (-not $seenFiles.Contains($rel)) {throw 'UNLISTED_FILE'}
  }
  foreach($need in @('payload/bridge/AzIdentResearch.exe','payload/host/AzIdentResearchHost.exe',
      'payload/tunnel/tunnel-client.exe','tools/Install-Ident006D.ps1')) {
   if (-not $seenFiles.Contains($need)) {throw 'REQUIRED_COMPONENT_MISSING'}
  }
  return @{temp=$temp;sha=$sha;manifest=$manifest}
 } catch {
  Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
  throw
 }
}

function Set-ProtectedAcl([string]$path,[Security.Principal.SecurityIdentifier]$serviceSid,
                           [bool]$isDirectory,[bool]$serviceWrite) {
 if ($isDirectory) {
  $acl=[Security.AccessControl.DirectorySecurity]::new()
  $inherit=[Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
 } else {
  $acl=[Security.AccessControl.FileSecurity]::new()
  $inherit=[Security.AccessControl.InheritanceFlags]::None
 }
 $acl.SetAccessRuleProtection($true,$false)
 $acl.SetOwner($adminSid)
 $svcRights=if($serviceWrite){[Security.AccessControl.FileSystemRights]::Modify}else{[Security.AccessControl.FileSystemRights]::ReadAndExecute}
 $rules=@(
  @{sid=$adminSid;rights=[Security.AccessControl.FileSystemRights]::FullControl},
  @{sid=$systemSid;rights=[Security.AccessControl.FileSystemRights]::FullControl},
  @{sid=$serviceSid;rights=$svcRights}
 )
 foreach($rule in $rules) {
  $ace=[Security.AccessControl.FileSystemAccessRule]::new(
   [Security.Principal.IdentityReference]$rule.sid,
   [Security.AccessControl.FileSystemRights]$rule.rights,
   $inherit,
   [Security.AccessControl.PropagationFlags]::None,
   [Security.AccessControl.AccessControlType]::Allow)
  $acl.AddAccessRule($ace)
 }
 Set-Acl -LiteralPath $path -AclObject $acl
}

function Set-AdminAcl([string]$path) {
 $acl=[Security.AccessControl.DirectorySecurity]::new()
 $acl.SetAccessRuleProtection($true,$false)
 $acl.SetOwner($adminSid)
 foreach($sid in @($adminSid,$systemSid)) {
  $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
   $sid,[Security.AccessControl.FileSystemRights]::FullControl,
   [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit',
   [Security.AccessControl.PropagationFlags]::None,
   [Security.AccessControl.AccessControlType]::Allow))
 }
 Set-Acl -LiteralPath $path -AclObject $acl
}

function Sc([string[]]$arguments) {
 $lines=& "$env:SystemRoot\System32\sc.exe" @arguments 2>&1
 if($LASTEXITCODE -ne 0) {throw "WINDOWS_SCM_ERROR_$LASTEXITCODE"}
 return $lines
}

switch($Mode) {
 'CIInspect' {
  if ($env:GITHUB_ACTIONS -cne 'true') {throw 'CI_ONLY'}
  $pkg=Inspect-Package $Package $ExpectedPackageSha256
  try {Write-Host "CI_PACKAGE_INSPECT=PASS; SHA256=$($pkg.sha); SQL=NONE; SERVICE=NONE"} finally {
   Remove-Item -LiteralPath $pkg.temp -Recurse -Force -ErrorAction SilentlyContinue
  }
 }
 'Preflight' {
  Assert-Host $false
  $pkg=Inspect-Package $Package $ExpectedPackageSha256
  try {Write-Host "PACKAGE_PREFLIGHT=PASS; SHA256=$($pkg.sha); SQL=NONE; SERVICE=NONE"} finally {
   Remove-Item -LiteralPath $pkg.temp -Recurse -Force -ErrorAction SilentlyContinue
  }
 }
 'Apply' {
  Assert-Host $true
  if ($ApprovalPhrase -cne 'AZ-IDENT-EX18-4-SOURCE-AUDITED-APPLY') {throw 'APPLY_NOT_AUTHORIZED'}
  if ((Test-Path -LiteralPath $root) -or (Get-Service -Name $serviceName -ErrorAction SilentlyContinue)) {throw 'EXISTING_INSTALLATION_NO_OVERWRITE'}
  $pkg=Inspect-Package $Package $ExpectedPackageSha256
  try {
   $payload=Join-Path $pkg.temp 'payload'
   New-Item -ItemType Directory -Path $root | Out-Null
   Set-AdminAcl $root
   foreach($folder in @($app,$state)) {
    New-Item -ItemType Directory -Path $folder | Out-Null
    Set-AdminAcl $folder
   }
   foreach($name in @('bridge','host','tunnel')) {
    $target=Join-Path $app $name
    New-Item -ItemType Directory -Path $target | Out-Null
    Set-AdminAcl $target
    Copy-Item -Path (Join-Path (Join-Path $payload $name) '*') -Destination $target -Recurse -Force
   }
   $hostExe=Join-Path $app 'host\AzIdentResearchHost.exe'
   $binPath='"'+$hostExe+'"'
   Sc @('create',$serviceName,'binPath=',$binPath,'type=','own',
     'start=','demand','obj=','NT SERVICE\AzIdentResearch') | Out-Null
   Sc @('sidtype',$serviceName,'unrestricted') | Out-Null
   $sid=([Security.Principal.NTAccount]::new('NT SERVICE','AzIdentResearch')).Translate([Security.Principal.SecurityIdentifier])
   foreach($folder in @($root,$app,$state)) {
    Set-ProtectedAcl $folder $sid $true ($folder -ceq $state)
   }
   foreach($name in @('bridge','host','tunnel')) {
    $folder=Join-Path $app $name
    Set-ProtectedAcl $folder $sid $true $false
    Get-ChildItem -LiteralPath $folder -File -Recurse | ForEach-Object {
     Set-ProtectedAcl $_.FullName $sid $false $false
    }
   }
   $source=[ordered]@{
    source_commit=$baseline;package_sha256=$pkg.sha;source_only=$true;
    sql_provisioned=$false;tunnel_key_provisioned=$false
   }
   [IO.File]::WriteAllText($marker,($source|ConvertTo-Json -Depth 3))
   Set-ProtectedAcl $marker $sid $false $false
   & $hostExe '--verify-staging'
   if($LASTEXITCODE -ne 0) {throw 'ACL_VERIFICATION_FAILED'}
   if((Get-Service -Name $serviceName).Status -ne 'Stopped') {throw 'SERVICE_NOT_STOPPED'}
   Write-Host 'APPLY=PASS; SERVICE=REGISTERED_STOPPED; PROVISION=NONE; SQL_CONNECTION=NONE; TUNNEL=NOT_STARTED'
  } finally {Remove-Item -LiteralPath $pkg.temp -Recurse -Force -ErrorAction SilentlyContinue}
 }
 'Verify' {
  Assert-Host $true
  if(-not (Test-Path -LiteralPath $marker)) {throw 'MARKER_MISSING'}
  $source=Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
  if($source.source_commit -cne $baseline) {throw 'SOURCE_MISMATCH'}
  $exe=Join-Path $app 'host\AzIdentResearchHost.exe'
  & $exe '--verify-staging'
  if($LASTEXITCODE -ne 0) {throw 'HOST_LAYOUT_INVALID'}
  $service=Get-Service -Name $serviceName -ErrorAction Stop
  Write-Host "INSTALL_VERIFY=PASS; SERVICE=$($service.Status); SQL_CONNECTION=NONE; TUNNEL_NOT_STARTED_BY_VERIFY=YES"
 }
 'Rollback' {
  Assert-Host $true
  if($ApprovalPhrase -cne 'AZ-IDENT-EX18-4-UNPROVISIONED-ROLLBACK') {throw 'ROLLBACK_NOT_AUTHORIZED'}
  if(-not (Test-Path -LiteralPath $marker)) {throw 'MARKER_MISSING'}
  $source=Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
  if($source.source_commit -cne $baseline) {throw 'SOURCE_MISMATCH'}
  $service=Get-Service -Name $serviceName -ErrorAction SilentlyContinue
  if($null -ne $service -and $service.Status -ne 'Stopped') {throw 'SERVICE_MUST_BE_STOPPED'}
  foreach($name in @('config.json','server.cer','sql.dpapi','tunnel-runtime.json','tunnel-runtime.dpapi')) {
   if(Test-Path -LiteralPath (Join-Path $app $name)) {throw 'PROVISIONED_INSTALLATION_PROTECTED'}
  }
  if(@(Get-ChildItem -LiteralPath $state -Force).Count -ne 0) {throw 'STATE_NOT_EMPTY'}
  $unexpected=@(Get-ChildItem -LiteralPath $root -Force | Where-Object {$_.Name -notin @('app','state','source-only-installation.json')})
  if($unexpected.Count -ne 0) {throw 'UNKNOWN_INSTALL_CONTENT'}
  if($null -ne $service) {Sc @('delete',$serviceName)|Out-Null}
  Remove-Item -LiteralPath $root -Recurse -Force
  Write-Host 'UNPROVISIONED_ROLLBACK=PASS; IDENT=UNTOUCHED'
 }
}
