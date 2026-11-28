# IDENT-006D SOURCE-ONLY installer candidate, NOT approved for deployment.
# No source IDENT settings, records, databases, or SQL permissions are modified.
# Windows PowerShell 5.1 and PowerShell 7.x: no .NET Core-only Path APIs.
[CmdletBinding()]
param(
 [ValidateSet('Preflight','CIInspect','CISafety','Apply','Recover','Verify','Rollback')]
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
$intent=Join-Path $root 'install-intent.json'
$marker=Join-Path $root 'source-only-installation.json'
$adminSid=[Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$systemSid=[Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$serviceSid=$null

function Assert-Host([bool]$requireAdmin) {
 if ($env:COMPUTERNAME -cne 'EX-18-4') {throw 'WRONG_COMPUTER'}
 if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitOperatingSystem) {throw 'WINDOWS_X64_REQUIRED'}
 $drive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($root))
 if($drive.DriveType -ne [IO.DriveType]::Fixed -or $drive.DriveFormat -cne 'NTFS'){throw 'LOCAL_NTFS_REQUIRED'}
 if($requireAdmin){
  $ident=[Security.Principal.WindowsIdentity]::GetCurrent()
  if(-not ([Security.Principal.WindowsPrincipal]::new($ident)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'ADMIN_REQUIRED'}
 }
}
function Assert-PlainPath([string]$p) {
 if([string]::IsNullOrWhiteSpace($p) -or $p -cnotmatch '^[A-Za-z]:\\' -or
    -not [string]::Equals([IO.Path]::GetFullPath($p),$p,[StringComparison]::OrdinalIgnoreCase)){
   throw 'CANONICAL_ABSOLUTE_LOCAL_PATH_REQUIRED'
 }
}
function Get-RelativeSafe([string]$base,[string]$full) {
 $basePath=[IO.Path]::GetFullPath($base).TrimEnd([char]'\')+'\'
 $fullPath=[IO.Path]::GetFullPath($full)
 if(-not $fullPath.StartsWith($basePath,[StringComparison]::OrdinalIgnoreCase)){throw 'PATH_OUTSIDE_ALLOWED_ROOT'}
 return $fullPath.Substring($basePath.Length).Replace('\','/')
}
function Assert-Ordinary([string]$path,[bool]$isDirectory) {
 $info=Get-Item -LiteralPath $path -Force -ErrorAction Stop
 if(($info.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
    ([bool]$info.PSIsContainer -ne $isDirectory)){throw 'NONORDINARY_PATH'}
 return $info
}
function Inspect-Package([string]$archivePath,[string]$sha) {
 if($sha -cnotmatch '^[a-f0-9]{64}$'){throw 'EXPECTED_SHA256_REQUIRED'}
 Assert-PlainPath $archivePath
 $item=Assert-Ordinary $archivePath $false
 if($item.Length -lt 1000 -or $item.Length -gt 700MB){throw 'PACKAGE_SIZE_INVALID'}
 if((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $sha){throw 'PACKAGE_SHA256_MISMATCH'}
 $temp=Join-Path $env:TEMP ('ident006d-package-'+[Guid]::NewGuid().ToString('N'))
 New-Item -ItemType Directory -Path $temp | Out-Null
 try {
  Add-Type -AssemblyName System.IO.Compression
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $zip=[IO.Compression.ZipFile]::OpenRead($archivePath)
  try {
   $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
   [long]$total=0
   foreach($entry in $zip.Entries){
    $name=$entry.FullName.Replace('\','/')
    if($name.StartsWith('/') -or $name.Contains('..') -or $name.Contains(':') -or $name -match '[\x00-\x1f]' -or
       $name -notmatch '^(payload|tools|manifest\.json)(/|$)' -or -not $seen.Add($name)){throw 'UNSAFE_ZIP_ENTRY'}
    $total += $entry.Length
    if($entry.Length -gt 250MB -or $total -gt 650MB){throw 'ZIP_EXPANSION_LIMIT'}
   }
  }finally{$zip.Dispose()}
  [IO.Compression.ZipFile]::ExtractToDirectory($archivePath,$temp)
  $manifestPath=Join-Path $temp 'manifest.json'
  $manifest=Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
  if($manifest.source_commit -cne $baseline -or $manifest.source_tree -cne $baselineTree -or
     $manifest.platform -cne 'win-x64' -or $manifest.mode -cne 'SOURCE_ONLY_CANDIDATE' -or
     $manifest.installation -cne 'NOT_EXECUTED' -or $manifest.real_ident -cne 'NONE'){throw 'MANIFEST_BASELINE'}
  $files=@($manifest.files)
  if($files.Count -lt 3 -or $files.Count -gt 1500){throw 'MANIFEST_COUNT'}
  $listed=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
  foreach($file in $files){
   $relative=[string]$file.path
   if($relative -cnotmatch '^(payload|tools)/[a-zA-Z0-9._/-]+$' -or
      $relative.Contains('..') -or -not $listed.Add($relative)){throw 'MANIFEST_PATH'}
   $path=Join-Path $temp ($relative.Replace('/',[IO.Path]::DirectorySeparatorChar))
   $found=Assert-Ordinary $path $false
   if($found.Length -ne [long]$file.length -or
      (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne [string]$file.sha256){throw 'MANIFEST_FILE_MISMATCH'}
  }
  $actual=@(Get-ChildItem -LiteralPath @((Join-Path $temp 'payload'),(Join-Path $temp 'tools')) -Recurse -File)
  if($actual.Count -ne $files.Count){throw 'MANIFEST_COUNT_MISMATCH'}
  foreach($file in $actual){
   $rel=Get-RelativeSafe $temp $file.FullName
   if(-not $listed.Contains($rel)){throw 'UNLISTED_FILE'}
  }
  foreach($required in @('payload/bridge/AzIdentResearch.exe','payload/host/AzIdentResearchHost.exe',
      'payload/tunnel/tunnel-client.exe','tools/Install-Ident006D.ps1')){
   if(-not $listed.Contains($required)){throw 'REQUIRED_COMPONENT_MISSING'}
  }
  return @{temp=$temp;sha=$sha;manifest=$manifest}
 }catch{
  Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
  throw
 }
}
function Invoke-ScExe([string[]]$arguments) {
 $result=& "$env:SystemRoot\System32\sc.exe" @arguments 2>&1
 if($LASTEXITCODE -ne 0){throw "WINDOWS_SCM_ERROR_$LASTEXITCODE"}
 return $result
}
function Get-ServiceSid {
 $lines=Invoke-ScExe -arguments @('showsid',$serviceName)
 $match=[regex]::Match(($lines -join ' '),'S-1-5-80-(?:\d+-){4}\d+')
 if(-not $match.Success){throw 'SERVICE_SID_PREFLIGHT_FAILED'}
 return ([Security.Principal.SecurityIdentifier]::new($match.Value))
}
function Set-ProtectedAcl([string]$path,[Security.Principal.SecurityIdentifier]$sid,
                          [bool]$directory,[bool]$writeService) {
 if($directory){
  $acl=[Security.AccessControl.DirectorySecurity]::new()
  $inherit=[Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
 }else{
  $acl=[Security.AccessControl.FileSecurity]::new()
  $inherit=[Security.AccessControl.InheritanceFlags]::None
 }
 $acl.SetAccessRuleProtection($true,$false)
 $acl.SetOwner($adminSid)
 $svcRights=if($writeService){[Security.AccessControl.FileSystemRights]::Modify}else{[Security.AccessControl.FileSystemRights]::ReadAndExecute}
 $rules=@(
  @{sid=$adminSid;rights=[Security.AccessControl.FileSystemRights]::FullControl},
  @{sid=$systemSid;rights=[Security.AccessControl.FileSystemRights]::FullControl},
  @{sid=$sid;rights=$svcRights}
 )
 foreach($rule in $rules){
  $ace=[Security.AccessControl.FileSystemAccessRule]::new(
   $rule.sid,$rule.rights,$inherit,[Security.AccessControl.PropagationFlags]::None,
   [Security.AccessControl.AccessControlType]::Allow)
  $acl.AddAccessRule($ace)
 }
 Set-Acl -LiteralPath $path -AclObject $acl
}
function Assert-ProtectedAcl([string]$path,[bool]$directory,[bool]$serviceWrite) {
 if($null -eq $serviceSid){throw 'SERVICE_SID_NOT_LOADED'}
 $null=Assert-Ordinary $path $directory
 $acl=Get-Acl -LiteralPath $path
 if(-not $acl.AreAccessRulesProtected -or
    $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -cne $adminSid.Value){throw 'ACL_PROTECTION_FAILED'}
 $rules=@($acl.GetAccessRules($true,$false,[Security.Principal.SecurityIdentifier]))
 if($rules.Count -ne 3){throw 'ACL_EXACT_PRINCIPALS_REQUIRED'}
 $inherit=if($directory){[Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'}else{[Security.AccessControl.InheritanceFlags]::None}
 $svcRights=if($serviceWrite){[Security.AccessControl.FileSystemRights]::Modify}else{[Security.AccessControl.FileSystemRights]::ReadAndExecute}
 $expected=@{}
 $expected[$adminSid.Value]=[int][Security.AccessControl.FileSystemRights]::FullControl
 $expected[$systemSid.Value]=[int][Security.AccessControl.FileSystemRights]::FullControl
 $expected[$serviceSid.Value]=[int]$svcRights
 $found=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
 foreach($rule in $rules){
  $sid=$rule.IdentityReference.Value
  if(-not $expected.ContainsKey($sid) -or -not $found.Add($sid) -or
     $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
     $rule.IsInherited -or $rule.InheritanceFlags -ne $inherit -or
     $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None -or
     [int]$rule.FileSystemRights -ne [int]$expected[$sid]){throw 'ACL_EXACT_RIGHTS_REQUIRED'}
 }
 if($found.Count -ne 3){throw 'ACL_EXACT_RIGHTS_REQUIRED'}
}
function Invoke-AclPreflight {
 $script:serviceSid=Get-ServiceSid
 $probe=Join-Path $env:TEMP ('ident006d-acl-'+[Guid]::NewGuid().ToString('N'))
 $probeFile=Join-Path $probe 'probe.dat'
 New-Item -ItemType Directory -Path $probe | Out-Null
 try{
  [IO.File]::WriteAllText($probeFile,'acl-probe')
  Set-ProtectedAcl $probe $serviceSid $true $false
  Set-ProtectedAcl $probeFile $serviceSid $false $false
  Assert-ProtectedAcl $probe $true $false
  Assert-ProtectedAcl $probeFile $false $false
 }finally{
  if(Test-Path -LiteralPath $probeFile){Remove-Item -LiteralPath $probeFile -Force}
  if(Test-Path -LiteralPath $probe){Remove-Item -LiteralPath $probe -Force}
 }
 Write-Host 'ACL_PREFLIGHT=PASS; TARGET_NOT_MODIFIED=YES'
}
function Get-ManagedMap($manifest) {
 $map=@{}
 foreach($file in @($manifest.files)){
  $rel=[string]$file.path
  if($rel -match '^payload/(bridge|host|tunnel)/(.+)$'){
   $dest='app/'+$rel.Substring(8)
   if($map.ContainsKey($dest)){throw 'DUPLICATE_INSTALL_TARGET'}
   $map[$dest]=@{source=$rel;length=[long]$file.length;sha=[string]$file.sha256}
  }elseif($rel -notmatch '^tools/'){throw 'UNEXPECTED_INSTALL_SOURCE'}
 }
 if(-not $map.ContainsKey('app/bridge/AzIdentResearch.exe') -or
    -not $map.ContainsKey('app/host/AzIdentResearchHost.exe') -or
    -not $map.ContainsKey('app/tunnel/tunnel-client.exe')){throw 'INSTALL_COMPONENTS_MISSING'}
 return ,$map
}
function Get-ExpectedDirs($map) {
 $dirs=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
 foreach($dir in @('app','state','app/bridge','app/host','app/tunnel')){$null=$dirs.Add($dir)}
 foreach($path in $map.Keys){
  $rel=[string]$path
  while($rel.Contains('/')){
   $rel=$rel.Substring(0,$rel.LastIndexOf('/'))
   $null=$dirs.Add($rel)
  }
 }
 return ,$dirs
}
function Get-TargetPath([string]$base,[string]$relative){
 if($relative -cnotmatch '^[A-Za-z0-9._/-]+$' -or $relative.Contains('..')){throw 'TARGET_RELATIVE_PATH'}
 return (Join-Path $base $relative.Replace('/',[IO.Path]::DirectorySeparatorChar))
}
function Assert-PayloadDigest([string]$path,$expected,[string]$reason){
 $file=Assert-Ordinary $path $false
 if($file.Length -ne [long]$expected.length -or
    (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne [string]$expected.sha){throw $reason}
}
function Assert-ManagedTree([string]$base,$manifest,[bool]$allowPartial){
 $map=Get-ManagedMap $manifest
 $dirs=Get-ExpectedDirs $map
 $parts=@{}
 foreach($rel in $map.Keys){$parts[$rel+'.ident006d-part']=$map[$rel]}
 $queue=[Collections.Generic.Queue[string]]::new()
 $queue.Enqueue($base)
 while($queue.Count -gt 0){
  $dir=$queue.Dequeue()
  $null=Assert-Ordinary $dir $true
  foreach($item in @(Get-ChildItem -LiteralPath $dir -Force)){
   if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'REPARSE_IN_INSTALL_TREE'}
   $rel=Get-RelativeSafe $base $item.FullName
   if($item.PSIsContainer){
    if(-not $dirs.Contains($rel)){throw 'UNRECOGNIZED_INSTALL_DIRECTORY'}
    $queue.Enqueue($item.FullName)
   }else{
    if($map.ContainsKey($rel)){
     $expected=$map[$rel]
     Assert-PayloadDigest $item.FullName $expected 'MODIFIED_INSTALLED_FILE'
    }elseif($parts.ContainsKey($rel) -and $allowPartial){
     # Filename alone is never provenance. Unknown/partial bytes must survive rollback.
     Assert-PayloadDigest $item.FullName $parts[$rel] 'UNOWNED_PARTIAL_CONTENT'
    }elseif($rel -notin @('install-intent.json','source-only-installation.json')){
     throw 'UNRECOGNIZED_INSTALL_FILE'
    }
   }
  }
 }
 return ,$map
}
function Assert-ExistingAclTree([string]$base,$manifest){
 $map=Get-ManagedMap $manifest
 $null=Assert-ManagedTree $base $manifest $true
 Assert-ProtectedAcl $base $true $false
 foreach($rel in (Get-ExpectedDirs $map)){
  $p=Get-TargetPath $base $rel
  if(Test-Path -LiteralPath $p){Assert-ProtectedAcl $p $true ($rel -ceq 'state')}
 }
 foreach($rel in $map.Keys){
  $target=Get-TargetPath $base $rel
  foreach($p in @($target,($target+'.ident006d-part'))){
   if(Test-Path -LiteralPath $p){Assert-ProtectedAcl $p $false $false}
  }
 }
 foreach($rel in @('install-intent.json','source-only-installation.json')){
  $p=Join-Path $base $rel
  if(Test-Path -LiteralPath $p){Assert-ProtectedAcl $p $false $false}
 }
}
function Assert-CompleteTree([string]$base,$manifest){
 $map=Get-ManagedMap $manifest
 $null=Assert-ManagedTree $base $manifest $false
 foreach($rel in (Get-ExpectedDirs $map)){
  if(-not (Test-Path -LiteralPath (Get-TargetPath $base $rel))){throw 'MISSING_INSTALL_DIRECTORY'}
 }
 foreach($rel in $map.Keys){
  $path=Get-TargetPath $base $rel
  if(-not (Test-Path -LiteralPath $path)){throw 'MISSING_INSTALLED_FILE'}
  Assert-PayloadDigest $path $map[$rel] 'MODIFIED_INSTALLED_FILE'
 }
 Assert-ExistingAclTree $base $manifest
}
function Assert-Journal([string]$sha,[bool]$allowMissing) {
 if(-not (Test-Path -LiteralPath $intent)){
  if($allowMissing){return $false}
  throw 'INSTALL_INTENT_MISSING'
 }
 $null=Assert-Ordinary $intent $false
 try{$j=Get-Content -LiteralPath $intent -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop}catch{throw 'INSTALL_INTENT_CORRUPT'}
 if($null -eq $j){throw 'INSTALL_INTENT_CORRUPT'}
 if($j.schema -ne 1 -or $j.source_commit -cne $baseline -or $j.package_sha256 -cne $sha -or
    $j.operation -cne 'EX18_4_SOURCE_ONLY_INSTALL'){throw 'INSTALL_INTENT_MISMATCH'}
 return $true
}
function Assert-Marker([string]$sha,[bool]$allowMissing) {
 if(-not (Test-Path -LiteralPath $marker)){
  if($allowMissing){return $false}
  throw 'FINAL_MARKER_MISSING'
 }
 $null=Assert-Ordinary $marker $false
 try{$m=Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop}catch{throw 'FINAL_MARKER_CORRUPT'}
 if($null -eq $m){throw 'FINAL_MARKER_CORRUPT'}
 if($m.source_commit -cne $baseline -or $m.package_sha256 -cne $sha -or
    $m.service -cne $serviceName){throw 'FINAL_MARKER_MISMATCH'}
 return $true
}
function Assert-ServiceDescriptor($svc,[string]$expectedExe){
 if($null -eq $svc){throw 'SERVICE_MISSING'}
 $expectedQuoted='"'+$expectedExe+'"'
 if($svc.State -cne 'Stopped' -or
    -not ([string]::Equals($svc.PathName,$expectedQuoted,[StringComparison]::OrdinalIgnoreCase) -or
          [string]::Equals($svc.PathName,$expectedExe,[StringComparison]::OrdinalIgnoreCase)) -or
    -not [string]::Equals($svc.StartName,'NT SERVICE\AzIdentResearch',[StringComparison]::OrdinalIgnoreCase) -or
    $svc.StartMode -cne 'Manual'){throw 'FOREIGN_OR_RUNNING_SERVICE'}
}
function Assert-ServiceOwned([bool]$allowMissing) {
 $svc=Get-CimInstance Win32_Service -Filter "Name='AzIdentResearch'" -ErrorAction Stop
 if($null -eq $svc){
  if($allowMissing){return $false}
  throw 'SERVICE_MISSING'
 }
 Assert-ServiceDescriptor $svc (Join-Path $app 'host\AzIdentResearchHost.exe')
 return $true
}
function Ensure-ProtectedDir([string]$target,[bool]$writeService){
 if(Test-Path -LiteralPath $target){
  $null=Assert-Ordinary $target $true
 }else{
  New-Item -ItemType Directory -Path $target -ErrorAction Stop | Out-Null
 }
 Set-ProtectedAcl $target $serviceSid $true $writeService
 Assert-ProtectedAcl $target $true $writeService
}
function Write-PrivateJson([string]$target,$data){
 if(Test-Path -LiteralPath $target){throw 'METADATA_ALREADY_PRESENT'}
 $json=ConvertTo-Json -InputObject $data -Depth 6
 [IO.File]::WriteAllText($target,$json,[Text.UTF8Encoding]::new($false))
 Set-ProtectedAcl $target $serviceSid $false $false
 Assert-ProtectedAcl $target $false $false
}
function Copy-ManagedPayload($pkg,$map,[string]$targetRoot){
 $dirs=Get-ExpectedDirs $map
 foreach($rel in @($dirs | Sort-Object {($_ -split '/').Count},Length)){
  $folder=Get-TargetPath $targetRoot $rel
  Ensure-ProtectedDir $folder ($rel -ceq 'state')
 }
 foreach($rel in @($map.Keys | Sort-Object)){
  $info=$map[$rel]
  $target=Get-TargetPath $targetRoot $rel
  $partial=$target+'.ident006d-part'
  if(Test-Path -LiteralPath $target){
   Assert-PayloadDigest $target $info 'MODIFIED_INSTALLED_FILE'
   continue
  }
  if(Test-Path -LiteralPath $partial){
   # Only a complete, verified temporary copy may be adopted, never deleted.
   Assert-PayloadDigest $partial $info 'UNOWNED_PARTIAL_CONTENT'
   Move-Item -LiteralPath $partial -Destination $target -ErrorAction Stop
   Set-ProtectedAcl $target $serviceSid $false $false
   continue
  }
  $src=Get-TargetPath $pkg.temp $info.source
  Copy-Item -LiteralPath $src -Destination $partial -ErrorAction Stop
  Set-ProtectedAcl $partial $serviceSid $false $false
  $copy=Assert-Ordinary $partial $false
  if($copy.Length -ne $info.length -or
     (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant() -cne $info.sha){throw 'FILE_COPY_SHA256_MISMATCH'}
  Move-Item -LiteralPath $partial -Destination $target -ErrorAction Stop
 }
}
function Complete-Installation($pkg,[bool]$resume){
 $sha=$pkg.sha
 $exists=Test-Path -LiteralPath $root
 if(-not $resume){
  if($exists -or (Assert-ServiceOwned $true)){throw 'EXISTING_INSTALLATION_NO_OVERWRITE'}
  Ensure-ProtectedDir $root $false
  $exists=$true
 }elseif(-not $exists){throw 'PARTIAL_INSTALLATION_NOT_FOUND'}
 if($resume){
  # Recovery only with a valid matching intent; corrupted and foreign journals
  # are preserved intact and never adopted, even if they are the only file.
  $null=Assert-Ordinary $root $true
  $null=Assert-Journal $sha $false
  if(Test-Path -LiteralPath $marker){
   $null=Assert-Marker $sha $false
   throw 'ALREADY_MARKED_INSTALLED'
  }
  Assert-ExistingAclTree $root $pkg.manifest
  # Foreign or running service stops recovery before ANY payload copy.
  $null=Assert-ServiceOwned $true
 }

 if(-not (Test-Path -LiteralPath $intent)){
  Write-PrivateJson $intent ([ordered]@{
   schema=1;operation='EX18_4_SOURCE_ONLY_INSTALL';source_commit=$baseline;package_sha256=$sha
  })
 }
 # All subsequent writes are confined to the known private tree. Every phase is resumable.
 Copy-ManagedPayload $pkg (Get-ManagedMap $pkg.manifest) $root
 # Nothing executes while any expected file is missing or any .part remains.
 Assert-CompleteTree $root $pkg.manifest
 $already=Assert-ServiceOwned $true
 if(-not $already){
  $exe=Join-Path $app 'host\AzIdentResearchHost.exe'
  Invoke-ScExe -arguments @('create',$serviceName,'binPath=',('"'+$exe+'"'),'type=','own',
    'start=','demand','obj=','NT SERVICE\AzIdentResearch') | Out-Null
 }
 Invoke-ScExe -arguments @('sidtype',$serviceName,'unrestricted') | Out-Null
 if(-not (Assert-ServiceOwned $false)){throw 'SERVICE_NOT_OWNED'}
 $hostExe=Join-Path $app 'host\AzIdentResearchHost.exe'
 & $hostExe '--verify-staging'
 if($LASTEXITCODE -ne 0){throw 'SERVICE_HOST_ACL_VERIFICATION'}
 Write-PrivateJson $marker ([ordered]@{
  source_commit=$baseline;package_sha256=$sha;service=$serviceName;source_only=$true;
  sql_provisioned=$false;tunnel_key_provisioned=$false
 })
 $null=Assert-Marker $sha $false
 Write-Host 'INSTALL_CANDIDATE_APPLY=PASS; SERVICE=STOPPED; SQL_CONNECTION=NONE; TUNNEL=NOT_STARTED'
}
function Rollback-KnownOnly($pkg){
 if(-not (Test-Path -LiteralPath $root)){throw 'INSTALL_NOT_FOUND'}
 $null=Assert-Ordinary $root $true
 $null=Assert-ManagedTree $root $pkg.manifest $true
 $null=Assert-Journal $pkg.sha $false
 $null=Assert-Marker $pkg.sha $true
 Assert-ExistingAclTree $root $pkg.manifest
 if(Assert-ServiceOwned $true){
  Invoke-ScExe -arguments @('delete',$serviceName) | Out-Null
  $gone=$false
  for($i=0;$i -lt 30;$i++){
   if($null -eq (Get-Service -Name $serviceName -ErrorAction SilentlyContinue)){$gone=$true;break}
   Start-Sleep -Seconds 1
  }
  if(-not $gone){throw 'SERVICE_DELETE_PENDING'}
 }
 $map=Get-ManagedMap $pkg.manifest
 foreach($rel in @($map.Keys | Sort-Object -Descending)){
  $target=Get-TargetPath $root $rel
  $partial=$target+'.ident006d-part'
  if(Test-Path -LiteralPath $target){
   Assert-PayloadDigest $target $map[$rel] 'MODIFIED_INSTALLED_FILE'
   Remove-Item -LiteralPath $target -Force -ErrorAction Stop
  }
  if(Test-Path -LiteralPath $partial){
   Assert-PayloadDigest $partial $map[$rel] 'UNOWNED_PARTIAL_CONTENT'
   Remove-Item -LiteralPath $partial -Force -ErrorAction Stop
  }
 }
 # Keep install-intent.json to permit safe retry after interrupted rollback.
 if(Test-Path -LiteralPath $marker){$null=Assert-Ordinary $marker $false;Remove-Item -LiteralPath $marker -Force -ErrorAction Stop}
 $dirs=Get-ExpectedDirs $map
 foreach($rel in @($dirs | Sort-Object {($_ -split '/').Count},Length -Descending)){
  $p=Get-TargetPath $root $rel
  if(Test-Path -LiteralPath $p){$null=Assert-Ordinary $p $true;Remove-Item -LiteralPath $p -Force -ErrorAction Stop}
 }
 # Remove the journal only after every known payload directory is empty.
 if(Test-Path -LiteralPath $intent){$null=Assert-Ordinary $intent $false;Remove-Item -LiteralPath $intent -Force -ErrorAction Stop}
 # Deliberately nonrecursive: unknown files appearing after preflight block root removal.
 $null=Assert-Ordinary $root $true
 Remove-Item -LiteralPath $root -Force -ErrorAction Stop
 Write-Host 'OWNED_FILES_ROLLBACK=PASS; ROOT_REMOVED_IF_EMPTY=YES; IDENT=UNTOUCHED'
}
function Assert-ExpectedFailure([scriptblock]$action,[string]$reason){
 $caught=$false
 try{$null=& $action}catch{
  if($_.Exception.Message -ceq $reason){$caught=$true}else{throw}
 }
 if(-not $caught){throw ('EXPECTED_FAILURE_NOT_SEEN_'+$reason)}
}
function Invoke-CISafety {
 if($env:GITHUB_ACTIONS -cne 'true'){throw 'CI_ONLY'}
 Invoke-AclPreflight
 $fixture=Join-Path $env:TEMP ('ident006d-synthetic-'+[Guid]::NewGuid().ToString('N'))
 $bridge=Join-Path $fixture 'app\bridge'
 $hostFolder=Join-Path $fixture 'app\host'
 $tunnelFolder=Join-Path $fixture 'app\tunnel'
 $stateTest=Join-Path $fixture 'state'
 $file=Join-Path $bridge 'AzIdentResearch.exe'
 $hostFile=Join-Path $hostFolder 'AzIdentResearchHost.exe'
 $tunnelFile=Join-Path $tunnelFolder 'tunnel-client.exe'
 $unknown=Join-Path $bridge 'patient-unknown.txt'
 $part=$file+'.ident006d-part'
 $brokenIntent=Join-Path $fixture 'install-intent.json'
 $sha=$null
 $savedRoot=$script:root
 $savedApp=$script:app
 $savedState=$script:state
 $savedIntent=$script:intent
 $savedMarker=$script:marker
 New-Item -ItemType Directory -Force -Path $bridge,$hostFolder,$tunnelFolder,$stateTest | Out-Null
 try{
  foreach($p in @($file,$hostFile,$tunnelFile)){[IO.File]::WriteAllText($p,'SYNTHETIC')}
  $sha=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
  $fake=[pscustomobject]@{files=@(
   [pscustomobject]@{path='payload/bridge/AzIdentResearch.exe';length=9;sha256=$sha},
   [pscustomobject]@{path='payload/host/AzIdentResearchHost.exe';length=9;sha256=$sha},
   [pscustomobject]@{path='payload/tunnel/tunnel-client.exe';length=9;sha256=$sha}
  )}
  foreach($p in @($fixture,(Join-Path $fixture 'app'),$bridge,$hostFolder,$tunnelFolder,$stateTest)){
   Set-ProtectedAcl $p $serviceSid $true ($p -ceq $stateTest)
  }
  foreach($p in @($file,$hostFile,$tunnelFile)){Set-ProtectedAcl $p $serviceSid $false $false}
  Assert-CompleteTree $fixture $fake
  Write-Host 'POSITIVE_COMPLETE_MANIFEST_AND_ACL=PASS'

  [IO.File]::WriteAllText($unknown,'DO_NOT_DELETE')
  Assert-ExpectedFailure {Assert-ManagedTree $fixture $fake $true} 'UNRECOGNIZED_INSTALL_FILE'
  if(-not (Test-Path -LiteralPath $unknown) -or -not (Test-Path -LiteralPath $file)){throw 'UNKNOWN_FILE_NOT_PRESERVED'}
  Remove-Item -LiteralPath $unknown -Force
  Write-Host 'NEGATIVE_UNKNOWN_FILE=PASS'

  # Counterexample 01: a forged .part of a legitimate filename is NOT ours.
  [IO.File]::WriteAllText($part,'TAMPERED!')
  Set-ProtectedAcl $part $serviceSid $false $false
  $forgedHash=(Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash.ToLowerInvariant()
  Assert-ExpectedFailure {Assert-ManagedTree $fixture $fake $true} 'UNOWNED_PARTIAL_CONTENT'
  Remove-Item -LiteralPath $file -Force
  $fakePkg=@{sha=('a'*64);manifest=$fake;temp=$fixture}
  Assert-ExpectedFailure {Copy-ManagedPayload $fakePkg (Get-ManagedMap $fake) $fixture} 'UNOWNED_PARTIAL_CONTENT'
  if(-not (Test-Path -LiteralPath $part) -or
     (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash.ToLowerInvariant() -cne $forgedHash){throw 'FORGED_PART_DESTROYED'}
  Remove-Item -LiteralPath $part -Force
  [IO.File]::WriteAllText($file,'SYNTHETIC')
  Set-ProtectedAcl $file $serviceSid $false $false
  Write-Host 'NEGATIVE_FOREIGN_PART_SHA256=PASS'

  # Counterexample 02: Recover against a truncated intent of a different SHA256.
  $script:root=$fixture
  $script:app=Join-Path $fixture 'app'
  $script:state=$stateTest
  $script:intent=$brokenIntent
  $script:marker=Join-Path $fixture 'source-only-installation.json'
  $foreignSha='f'*64
  $requestedSha='a'*64
  $truncated='{"schema":1,"operation":"EX18_4_SOURCE_ONLY_INSTALL","source_commit":"'+$baseline+'","package_sha256":"'+$foreignSha+'"'
  [IO.File]::WriteAllText($brokenIntent,$truncated)
  Set-ProtectedAcl $brokenIntent $serviceSid $false $false
  $originalIntentHash=(Get-FileHash -LiteralPath $brokenIntent -Algorithm SHA256).Hash.ToLowerInvariant()
  Assert-ExpectedFailure {Complete-Installation $fakePkg $true} 'INSTALL_INTENT_CORRUPT'
  if(-not (Test-Path -LiteralPath $brokenIntent) -or
    (Get-FileHash -LiteralPath $brokenIntent -Algorithm SHA256).Hash.ToLowerInvariant() -cne $originalIntentHash){throw 'FOREIGN_INTENT_DESTROYED'}
  $foreignJson=ConvertTo-Json -Compress -InputObject ([ordered]@{
   schema=1;operation='EX18_4_SOURCE_ONLY_INSTALL';source_commit=$baseline;package_sha256=$foreignSha
  })
  [IO.File]::WriteAllText($brokenIntent,$foreignJson)
  Assert-ExpectedFailure {Complete-Installation $fakePkg $true} 'INSTALL_INTENT_MISMATCH'
  if(-not (Test-Path -LiteralPath $brokenIntent)){throw 'FOREIGN_VALID_INTENT_DESTROYED'}
  Remove-Item -LiteralPath $brokenIntent -Force
  Write-Host 'NEGATIVE_FOREIGN_TRUNCATED_INTENT=PASS; FOREIGN_VALID_INTENT=PASS'
  $script:root=$savedRoot;$script:app=$savedApp;$script:state=$savedState
  $script:intent=$savedIntent;$script:marker=$savedMarker

  # Counterexample 03: exactly three SIDs is insufficient when service has FullControl.
  $acl=Get-Acl -LiteralPath $bridge
  $serviceRules=@($acl.GetAccessRules($true,$false,[Security.Principal.SecurityIdentifier]) |
    Where-Object {$_.IdentityReference.Value -ceq $serviceSid.Value})
  if($serviceRules.Count -ne 1){throw 'ACL_TEST_INVALID'}
  $null=$acl.RemoveAccessRuleSpecific($serviceRules[0])
  $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
   $serviceSid,[Security.AccessControl.FileSystemRights]::FullControl,
   [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit',
   [Security.AccessControl.PropagationFlags]::None,
   [Security.AccessControl.AccessControlType]::Allow))
  Set-Acl -LiteralPath $bridge -AclObject $acl
  Assert-ExpectedFailure {Assert-ProtectedAcl $bridge $true $false} 'ACL_EXACT_RIGHTS_REQUIRED'
  Assert-ExpectedFailure {Assert-ExistingAclTree $fixture $fake} 'ACL_EXACT_RIGHTS_REQUIRED'
  Set-ProtectedAcl $bridge $serviceSid $true $false
  Assert-CompleteTree $fixture $fake
  Write-Host 'NEGATIVE_SERVICE_FULLCONTROL_ACL=PASS'

  # Counterexample 05: foreign Win32_Service and altered host fail before execution.
  $expectedExe=$hostFile
  $service=[pscustomobject]@{
   State='Stopped';PathName=('"'+$expectedExe+'"');
   StartName='NT SERVICE\AzIdentResearch';StartMode='Manual'
  }
  Assert-ServiceDescriptor $service $expectedExe
  $service.StartName='LocalSystem'
  Assert-ExpectedFailure {Assert-ServiceDescriptor $service $expectedExe} 'FOREIGN_OR_RUNNING_SERVICE'
  $service.StartName='NT SERVICE\AzIdentResearch'
  $service.PathName='C:\Windows\system32\foreign.exe'
  Assert-ExpectedFailure {Assert-ServiceDescriptor $service $expectedExe} 'FOREIGN_OR_RUNNING_SERVICE'
  [IO.File]::WriteAllText($hostFile,'TAMPERED!')
  Assert-ExpectedFailure {Assert-CompleteTree $fixture $fake} 'MODIFIED_INSTALLED_FILE'
  if(-not (Test-Path -LiteralPath $hostFile)){throw 'TAMPERED_HOST_DESTROYED'}
  Write-Host 'NEGATIVE_FOREIGN_SERVICE=PASS; NEGATIVE_HOST_TAMPER=PASS'
  Write-Host 'CI_SAFETY=PASS; OPEN_FINDINGS=NEGATIVE_TESTED; REAL_IDENT=NONE; SERVICE_INSTALLATION=NONE'
 }finally{
  $script:root=$savedRoot;$script:app=$savedApp;$script:state=$savedState
  $script:intent=$savedIntent;$script:marker=$savedMarker
  foreach($p in @($unknown,$part,$brokenIntent,$file,$hostFile,$tunnelFile)){
   if(Test-Path -LiteralPath $p){Remove-Item -LiteralPath $p -Force}
  }
  foreach($p in @($bridge,$hostFolder,$tunnelFolder,(Join-Path $fixture 'app'),$stateTest,$fixture)){
   if(Test-Path -LiteralPath $p){Remove-Item -LiteralPath $p -Force}
  }
 }
}

switch($Mode){
 'CIInspect' {
  if($env:GITHUB_ACTIONS -cne 'true'){throw 'CI_ONLY'}
  $pkg=Inspect-Package $Package $ExpectedPackageSha256
  try{Write-Host "CI_PACKAGE_INSPECT=PASS; SHA256=$($pkg.sha); SQL=NONE; SERVICE=NONE"}finally{
   Remove-Item -LiteralPath $pkg.temp -Recurse -Force -ErrorAction SilentlyContinue
  }
 }
 'CISafety' {Invoke-CISafety}
 'Preflight' {
  Assert-Host $true
  if((Test-Path -LiteralPath $root) -or (Assert-ServiceOwned $true)){throw 'EXISTING_INSTALLATION_REQUIRES_RECOVERY'}
  $pkg=Inspect-Package $Package $ExpectedPackageSha256
  try{
   Invoke-AclPreflight
   Write-Host "PACKAGE_PREFLIGHT=PASS; SHA256=$($pkg.sha); TARGET_CHANGED=NO"
  }finally{Remove-Item -LiteralPath $pkg.temp -Recurse -Force -ErrorAction SilentlyContinue}
 }
 'Apply' {
  Assert-Host $true
  if($ApprovalPhrase -cne 'AZ-IDENT-EX18-4-SOURCE-AUDITED-APPLY'){throw 'APPLY_NOT_AUTHORIZED'}
  if((Test-Path -LiteralPath $root) -or (Assert-ServiceOwned $true)){throw 'EXISTING_INSTALLATION_NO_OVERWRITE'}
  $pkg=Inspect-Package $Package $ExpectedPackageSha256
  try{
   Invoke-AclPreflight
   Complete-Installation $pkg $false
  }finally{Remove-Item -LiteralPath $pkg.temp -Recurse -Force -ErrorAction SilentlyContinue}
 }
 'Recover' {
  Assert-Host $true
  if($ApprovalPhrase -cne 'AZ-IDENT-EX18-4-AUDITED-RECOVER'){throw 'RECOVERY_NOT_AUTHORIZED'}
  $pkg=Inspect-Package $Package $ExpectedPackageSha256
  try{
   Invoke-AclPreflight
   Complete-Installation $pkg $true
  }finally{Remove-Item -LiteralPath $pkg.temp -Recurse -Force -ErrorAction SilentlyContinue}
 }
 'Verify' {
  Assert-Host $true
  # The host binary is untrusted until its exact packaged SHA256, ACL,
  # complete installed tree and Win32_Service descriptor have all passed.
  $pkg=Inspect-Package $Package $ExpectedPackageSha256
  try{
   Invoke-AclPreflight
   $null=Assert-Ordinary $root $true
   $null=Assert-Journal $pkg.sha $false
   $null=Assert-Marker $pkg.sha $false
   Assert-CompleteTree $root $pkg.manifest
   $null=Assert-ServiceOwned $false
   $expected=(Get-ManagedMap $pkg.manifest)['app/host/AzIdentResearchHost.exe']
   $exe=Join-Path $app 'host\AzIdentResearchHost.exe'
   Assert-PayloadDigest $exe $expected 'HOST_SHA256_MISMATCH'
   & $exe '--verify-staging'
   if($LASTEXITCODE -ne 0){throw 'HOST_LAYOUT_INVALID'}
   Write-Host 'INSTALL_VERIFY=PASS; SERVICE=STOPPED; HOST_SHA256=PASS; MANIFEST=PASS; ACL=PASS; SQL_CONNECTION=NONE; TUNNEL_NOT_STARTED_BY_VERIFY=YES'
  }finally{Remove-Item -LiteralPath $pkg.temp -Recurse -Force -ErrorAction SilentlyContinue}
 }
 'Rollback' {
  Assert-Host $true
  if($ApprovalPhrase -cne 'AZ-IDENT-EX18-4-UNPROVISIONED-ROLLBACK'){throw 'ROLLBACK_NOT_AUTHORIZED'}
  $pkg=Inspect-Package $Package $ExpectedPackageSha256
  try{
   Invoke-AclPreflight
   Rollback-KnownOnly $pkg
  }finally{Remove-Item -LiteralPath $pkg.temp -Recurse -Force -ErrorAction SilentlyContinue}
 }
}
