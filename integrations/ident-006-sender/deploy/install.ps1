# IDENT-006C first installation, Windows PowerShell 5.1, elevated admin only.
# Source candidate. Never download, change SQL rights, change SMB shares or
# enable the service without the separate explicit Enable action.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('Plan','Stage','Provision','Enable','Disable')][string]$Mode,
    [Parameter(Mandatory=$true)][string]$ExpectedComputerName,
    [Parameter(Mandatory=$true)][string]$ExpectedMachineGuidSha256,
    [Parameter(Mandatory=$true)][string]$ManifestSha256,
    [string]$PackageDirectory,
    [string]$Template,
    [string]$InitialPacket,
    [string]$Certificate,
    [string]$Confirm
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$name='AzIdent006C'
$serviceAccount='NT SERVICE\'+$name
$root=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::CommonApplicationData)) 'AZ-IDENT006C'
$app=Join-Path $root 'app'
$state=Join-Path $root 'state'
$journal=Join-Path $state 'journal'
$exe=Join-Path $app 'AzIdentSender.exe'
$binPath=$exe+' --service'
$adminSid=[Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$systemSid=[Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$trustedInstaller='S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'
$serviceSid=$null
$createdService=$false

function Require([bool]$Condition,[string]$Reason) { if(-not $Condition){throw $Reason} }
function Hash-Bytes([byte[]]$Bytes) {
    $h=[Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($h.ComputeHash($Bytes)).Replace('-','').ToLowerInvariant() }
    finally {$h.Dispose()}
}
function Canonical-Path([string]$Path) {
    Require (-not [string]::IsNullOrWhiteSpace($Path)) 'PATH_REQUIRED'
    Require ($Path -match '^[A-Za-z]:\\' -and $Path.Substring(2) -notmatch '[:/]|(?:^|\\)[^\\]*[. ](?:\\|$)|\\{2,}') 'LOCAL_PATH_REQUIRED'
    $p=[IO.Path]::GetFullPath($Path)
    Require ($p -ieq $Path) 'CANONICAL_PATH_REQUIRED'
    $drive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($p))
    Require ($drive.DriveType -eq 'Fixed' -and $drive.DriveFormat -eq 'NTFS') 'FIXED_NTFS_REQUIRED'
    return $p
}
function Assert-NoReparse([string]$Path) {
    $p=Canonical-Path $Path
    $item=Get-Item -LiteralPath $p -Force
    while($null -ne $item) {
        Require (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'REPARSE_POINT'
        if($item -is [IO.FileInfo]){$item=$item.Directory}else{$item=$item.Parent}
    }
}
function Assert-Ancestors([string]$Path) {
    Assert-NoReparse $Path
    $allowed=@($adminSid.Value,$systemSid.Value,$trustedInstaller)
    $danger=[int]([Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor [Security.AccessControl.FileSystemRights]::Delete -bor [Security.AccessControl.FileSystemRights]::ChangePermissions -bor [Security.AccessControl.FileSystemRights]::TakeOwnership -bor [Security.AccessControl.FileSystemRights]::WriteAttributes -bor [Security.AccessControl.FileSystemRights]::WriteExtendedAttributes) -bor 0x50000000
    $dir=[IO.DirectoryInfo]::new($Path)
    while($null -ne $dir) {
        $acl=$dir.GetAccessControl()
        Require ($acl.AreAccessRulesCanonical -and $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -in $allowed) 'ANCESTOR_OWNER'
        foreach($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
            if($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $allowed -and ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -eq 0) {
                Require (([int]$rule.FileSystemRights -band $danger) -eq 0) 'ANCESTOR_ACL'
            }
        }
        $dir=$dir.Parent
    }
}
function Assert-Shares {
    $shares=@(Get-CimInstance -ClassName Win32_Share -Namespace root/cimv2 -ErrorAction Stop)
    foreach($share in $shares) {
        if(([uint32]$share.Type -band 65535) -ne 0){continue}
        Require (-not [string]::IsNullOrWhiteSpace($share.Path)) 'SHARE_PATH'
        $p=[IO.Path]::GetFullPath($share.Path).TrimEnd('\')
        $driveAdmin=([string]$share.Name -match '^[A-Za-z]\$$') -and ($p -ieq ([string]$share.Name).Replace('$',':'))
        $windowsAdmin=([string]$share.Name -ieq 'ADMIN$') -and ($p -ieq [Environment]::GetFolderPath([Environment+SpecialFolder]::Windows).TrimEnd('\'))
        Require ([uint32]$share.Type -eq 2147483648 -and ($driveAdmin -or $windowsAdmin)) 'ORDINARY_DISK_SHARE'
    }
}
function Get-BridgeService { return Get-CimInstance -ClassName Win32_Service -Filter "Name='$name'" -ErrorAction Stop }
function Invoke-Sc([string[]]$ScArgs) {
    $sc=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::System)) 'sc.exe'
    & $sc @ScArgs | Out-Null
    Require ($LASTEXITCODE -eq 0) 'SCM_OPERATION_FAILED'
}
function Assert-ServiceBinding {
    $svc=Get-BridgeService
    Require ($null -ne $svc -and $svc.StartName -ieq $serviceAccount -and $svc.PathName -ceq $binPath) 'SERVICE_BINDING'
    Require ($svc.ServiceType -eq 'Own Process') 'SERVICE_TYPE'
    return $svc
}
function New-PrivateDirectory([string]$Path,[bool]$Writable) {
    Require (-not [IO.Directory]::Exists($Path) -and -not [IO.File]::Exists($Path)) 'DIRECTORY_EXISTS'
    $acl=[Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($adminSid);$acl.SetAccessRuleProtection($true,$false)
    $inherit=[Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit
    foreach($sid in @($adminSid,$systemSid,$serviceSid)) {
        $rights=if($sid -eq $serviceSid){if($Writable){[Security.AccessControl.FileSystemRights]::Modify}else{[Security.AccessControl.FileSystemRights]::ReadAndExecute}}else{[Security.AccessControl.FileSystemRights]::FullControl}
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,$rights,$inherit,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow))
    }
    [void][IO.Directory]::CreateDirectory($Path,$acl)
}
function Assert-AppAcl($Item) {
    Require (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'INSTALLED_REPARSE_POINT'
    $acl=$Item.GetAccessControl()
    Require ($acl.AreAccessRulesCanonical -and $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -in @($adminSid.Value,$systemSid.Value)) 'INSTALLED_OWNER'
    $readMask=[int][Security.AccessControl.FileSystemRights]::ReadAndExecute -bor [int][Security.AccessControl.FileSystemRights]::Synchronize
    $seen=$false
    foreach($r in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
        Require ($r.AccessControlType -eq 'Allow' -and $r.IdentityReference.Value -in @($adminSid.Value,$systemSid.Value,$serviceSid.Value)) 'INSTALLED_ACL'
        if($r.IdentityReference.Value -eq $serviceSid.Value) {
            Require ((([int]$r.FileSystemRights) -band (-bnot $readMask)) -eq 0) 'SERVICE_WRITE_TO_APP'
            if(($r.FileSystemRights -band [Security.AccessControl.FileSystemRights]::ReadData) -ne 0){$seen=$true}
        }
    }
    Require $seen 'SERVICE_APP_READ'
}
function Write-PrivateFile([string]$Path,[byte[]]$Bytes) {
    $acl=[Security.AccessControl.FileSecurity]::new()
    $acl.SetOwner($adminSid);$acl.SetAccessRuleProtection($true,$false)
    foreach($sid in @($adminSid,$systemSid,$serviceSid)) {
        $rights=if($sid -eq $serviceSid){[Security.AccessControl.FileSystemRights]::ReadAndExecute}else{[Security.AccessControl.FileSystemRights]::FullControl}
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,$rights,[Security.AccessControl.AccessControlType]::Allow))
    }
    $f=[IO.FileStream]::new($Path,[IO.FileMode]::CreateNew,[Security.AccessControl.FileSystemRights]::FullControl,[IO.FileShare]::None,4096,[IO.FileOptions]::WriteThrough,$acl)
    try{$f.Write($Bytes,0,$Bytes.Length);$f.Flush($true)}finally{$f.Dispose()}
}
function Read-Manifest([string]$Folder,[bool]$Installed) {
    Assert-NoReparse $Folder
    $manifestPath=Join-Path $Folder 'package-manifest.json'
    Assert-NoReparse $manifestPath
    $raw=[IO.File]::ReadAllBytes($manifestPath)
    Require ($raw.Length -le 262144 -and (Hash-Bytes $raw) -ceq $ManifestSha256) 'PACKAGE_MANIFEST_HASH'
    $manifest=[Text.Encoding]::UTF8.GetString($raw)|ConvertFrom-Json
    $files=@{}
    foreach($entry in $manifest.PSObject.Properties) {
        $rel=[string]$entry.Name
        Require ($rel -cmatch '^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$' -and @($rel.Split('/')|Where-Object {$_ -in @('.','..') -or $_.EndsWith('.')}).Count -eq 0 -and $rel -notin @('package-manifest.json','config.json','server.cer','sql.dpapi','token.dpapi') -and -not $files.ContainsKey($rel) -and [string]$entry.Value -cmatch '^[0-9a-f]{64}$') 'PACKAGE_ENTRY'
        $files[$rel]=[string]$entry.Value
    }
    Require ($files.Count -ge 10 -and $files.Count -le 2048 -and $files.ContainsKey('AzIdentSender.exe') -and $files.ContainsKey('AzIdentSender.dll')) 'PACKAGE_SCOPE'
    $allowed=@($files.Keys)+@('package-manifest.json')
    if($Installed){$allowed+=@('config.json','server.cer','sql.dpapi','token.dpapi')}
    $items=@(Get-ChildItem -LiteralPath $Folder -Force -Recurse)
    foreach($item in $items) {
        Require (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'PACKAGE_REPARSE'
        if($Installed){Assert-AppAcl $item}
        if(-not $item.PSIsContainer) {
            $rel=$item.FullName.Substring($Folder.Length+1).Replace('\','/')
            Require ($rel -cin $allowed) 'PACKAGE_EXTRA_FILE'
        }
    }
    foreach($rel in $files.Keys) {
        $file=Join-Path $Folder $rel.Replace('/','\')
        Require ([IO.File]::Exists($file) -and (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $files[$rel]) 'PACKAGE_FILE_HASH'
    }
    return @{Files=$files;Raw=$raw}
}
function Copy-VerifiedPackage($Manifest) {
    foreach($rel in @($Manifest.Files.Keys|Sort-Object)) {
        $src=Join-Path $PackageDirectory $rel.Replace('/','\')
        $dst=Join-Path $app $rel.Replace('/','\')
        $parent=Split-Path -Parent $dst
        $parts=$rel.Split('/')
        $dir=$app
        for($i=0;$i -lt $parts.Length-1;$i++) {
            $dir=Join-Path $dir $parts[$i]
            if(-not [IO.Directory]::Exists($dir)){New-PrivateDirectory $dir $false}
        }
        $raw=[IO.File]::ReadAllBytes($src)
        Require ((Hash-Bytes $raw) -ceq $Manifest.Files[$rel]) 'PACKAGE_COPY_HASH'
        Write-PrivateFile $dst $raw
    }
    Write-PrivateFile (Join-Path $app 'package-manifest.json') $Manifest.Raw
}
try {
    Require ($PSVersionTable.PSEdition -eq 'Desktop' -and [Environment]::Is64BitProcess) 'WINDOWS_PS51_X64_REQUIRED'
    $principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    Require ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) 'ADMIN_REQUIRED'
    Require ($env:COMPUTERNAME -ieq $ExpectedComputerName -and $ExpectedMachineGuidSha256 -cmatch '^[0-9a-f]{64}$') 'WRONG_HOST'
    $guid=[string](Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid).MachineGuid
    Require ((Hash-Bytes ([Text.Encoding]::UTF8.GetBytes($guid))) -ceq $ExpectedMachineGuidSha256) 'WRONG_MACHINE'
    Require ($ManifestSha256 -cmatch '^[0-9a-f]{64}$' -and $root -notmatch '[\s"]') 'MANIFEST_OR_SERVICE_PATH'
    Assert-Ancestors (Split-Path -Parent $root)
    Assert-Shares
    # Process-local only; reject inherited code-injection/profiling settings.
    foreach($v in @('DOTNET_STARTUP_HOOKS','DOTNET_ADDITIONAL_DEPS','DOTNET_SHARED_STORE','CORECLR_ENABLE_PROFILING','CORECLR_PROFILER','CORECLR_PROFILER_PATH','COR_ENABLE_PROFILING','COR_PROFILER','COR_PROFILER_PATH')) {
        [Environment]::SetEnvironmentVariable($v,$null,[EnvironmentVariableTarget]::Process)
    }
    if($Mode -in @('Plan','Stage')) {
        Require ($null -eq (Get-BridgeService) -and -not (Test-Path -LiteralPath $root)) 'INSTALL_TARGET_ALREADY_EXISTS'
        $PackageDirectory=Canonical-Path $PackageDirectory
        $m=Read-Manifest $PackageDirectory $false
        if($Mode -eq 'Plan') {
            Require ([string]::IsNullOrEmpty($Confirm)) 'PLAN_NO_CONFIRMATION'
            Write-Output 'INSTALL_PLAN=PASS; CHANGES=NONE; SQL=NONE; HTTP=NONE'
            exit 0
        }
        Require ($Confirm -ceq 'IDENT-006C-STAGE') 'EXPLICIT_STAGE_APPROVAL_REQUIRED'
        Invoke-Sc @('create',$name,'binPath=',$binPath,'start=','disabled','obj=',$serviceAccount,'DisplayName=','IDENT Bridge sender')
        $createdService=$true
        Invoke-Sc @('sidtype',$name,'unrestricted')
        $null=Assert-ServiceBinding
        $serviceSid=([Security.Principal.NTAccount]::new($serviceAccount)).Translate([Security.Principal.SecurityIdentifier])
        New-PrivateDirectory $root $false
        New-PrivateDirectory $app $false
        New-PrivateDirectory $state $true
        New-PrivateDirectory $journal $true
        Copy-VerifiedPackage $m
        $null=Read-Manifest $app $true
        Invoke-Sc @('failure',$name,'reset=','86400','actions=','restart/30000/restart/60000/restart/300000')
        Invoke-Sc @('failureflag',$name,'1')
        $svc=Assert-ServiceBinding
        Require ($svc.State -eq 'Stopped' -and $svc.StartMode -eq 'Disabled') 'STAGE_MUST_STAY_DISABLED'
        Write-Output 'INSTALL_STAGE=PASS; SERVICE=DISABLED; PROVISION=NOT_RUN; SQL=NONE; HTTP=NONE'
        exit 0
    }
    $svc=Assert-ServiceBinding
    $serviceSid=([Security.Principal.NTAccount]::new($serviceAccount)).Translate([Security.Principal.SecurityIdentifier])
    if($Mode -eq 'Provision') {
        Require ($Confirm -ceq 'IDENT-006C-PROVISION' -and $svc.State -eq 'Stopped' -and $svc.StartMode -eq 'Disabled') 'EXPLICIT_PROVISION_APPROVAL_REQUIRED'
        Assert-AppAcl (Get-Item -LiteralPath $root -Force)
        Assert-AppAcl (Get-Item -LiteralPath $app -Force)
        $null=Read-Manifest $app $true
        foreach($p in @($Template,$InitialPacket,$Certificate)){Assert-NoReparse $p;Require ([IO.File]::Exists($p)) 'PROVISION_INPUT_FILE'}
        Require ((Get-Item -LiteralPath $Template).Length -gt 0 -and (Get-Item -LiteralPath $Template).Length -le 8192 -and (Get-Item -LiteralPath $InitialPacket).Length -gt 0 -and (Get-Item -LiteralPath $InitialPacket).Length -le 524288 -and (Get-Item -LiteralPath $Certificate).Length -gt 0 -and (Get-Item -LiteralPath $Certificate).Length -le 65536) 'PROVISION_INPUT_SIZE'
        & $exe --provision $Template $InitialPacket $Certificate
        Require ($LASTEXITCODE -eq 0) 'PROVISION_FAILED'
        & $exe --check-local
        Require ($LASTEXITCODE -eq 0) 'LOCAL_CHECK_FAILED'
        Write-Output 'PROVISION=PASS; SERVICE=DISABLED; ORIGINAL_PACKET=UNCHANGED; SQL=NONE; HTTP=NONE'
        exit 0
    }
    if($Mode -eq 'Disable') {
        Require ($Confirm -ceq 'IDENT-006C-DISABLE') 'EXPLICIT_DISABLE_APPROVAL_REQUIRED'
        Invoke-Sc @('config',$name,'start=','disabled')
        if($svc.State -ne 'Stopped') {
            Stop-Service -Name $name -ErrorAction Stop
            (Get-Service -Name $name).WaitForStatus([ServiceProcess.ServiceControllerStatus]::Stopped,[TimeSpan]::FromSeconds(150))
        }
        $svc=Assert-ServiceBinding
        Require ($svc.State -eq 'Stopped' -and $svc.StartMode -eq 'Disabled') 'STOP_FAILED'
        Write-Output 'DISABLE=PASS; DATA_PRESERVED=YES'
        exit 0
    }
    Require ($Confirm -ceq 'IDENT-006C-ENABLE' -and $svc.State -eq 'Stopped' -and $svc.StartMode -eq 'Disabled') 'EXPLICIT_ENABLE_APPROVAL_REQUIRED'
    Assert-AppAcl (Get-Item -LiteralPath $root -Force)
    Assert-AppAcl (Get-Item -LiteralPath $app -Force)
    $null=Read-Manifest $app $true
    & $exe --check-local
    Require ($LASTEXITCODE -eq 0) 'LOCAL_CHECK_FAILED'
    try {
        Invoke-Sc @('config',$name,'start=','delayed-auto')
        Start-Service -Name $name
        (Get-Service -Name $name).WaitForStatus([ServiceProcess.ServiceControllerStatus]::Running,[TimeSpan]::FromSeconds(30))
        $svc=Assert-ServiceBinding
        Require ($svc.State -eq 'Running' -and $svc.StartMode -eq 'Auto') 'SERVICE_START_FAILED'
        Write-Output 'ENABLE=PASS; AUTOSTART=YES; VERIFY_STATUS_AND_SERVER_ACK'
    } catch {
        Invoke-Sc @('config',$name,'start=','disabled')
        Stop-Service -Name $name -ErrorAction SilentlyContinue
        throw 'ENABLE_FAILED_DISABLED'
    }
    exit 0
} catch {
    if($createdService) {
        try{Invoke-Sc @('config',$name,'start=','disabled')}catch{}
        Write-Output 'INSTALL_INCOMPLETE; NEW_FILES_PRESERVED; DO_NOT_ENABLE'
    }
    $reason=$_.Exception.Message
    if($reason -cnotmatch '^[A-Z_]+$'){$reason='LOCAL_VALIDATION_OR_RUNTIME'}
    Write-Output ('STOP='+$reason)
    exit 1
}
