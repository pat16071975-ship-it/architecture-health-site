# Disposable Windows CI only. Exercises the actual installer functions and
# the actual C# runtime ancestor guard against real, on-disk NTFS DACLs.
param([Parameter(Mandatory=$true)][string]$TestExe)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or $env:GITHUB_REF -ne 'refs/heads/test/ident-006c-sender-20261126' -or $PSVersionTable.PSEdition -ne 'Desktop'){throw 'DISPOSABLE_WINDOWS_PS51_REQUIRED'}
$source=Split-Path -Parent $PSScriptRoot
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $source 'deploy/install.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count -ne 0){throw 'INSTALLER_SYNTAX'}
# Load definitions from the candidate, not a duplicated implementation.
$functions=@('Require','Canonical-Path','Assert-NoReparse','Assert-Ancestors','New-DirectorySecurity','Assert-CreatedDirectory','Assert-DirectoryParents','New-PrivateDirectory','Assert-InstalledDirectoryLayout')
foreach($name in $functions){
    $found=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$false))
    if($found.Count -ne 1){throw ('MISSING_EXACT_FUNCTION_'+$name)}
    . ([scriptblock]::Create($found[0].Extent.Text))
}
$adminSid=[Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$systemSid=[Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$trustedInstaller='S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'
$serviceSid=([Security.Principal.NTAccount]::new('NT SERVICE\AzIdent006C')).Translate([Security.Principal.SecurityIdentifier])
$usersSid=[Security.Principal.SecurityIdentifier]::new('S-1-5-32-545')
$inherit=[Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit
$none=[Security.AccessControl.PropagationFlags]::None
$allow=[Security.AccessControl.AccessControlType]::Allow
$parent=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)) ('IDENT006C-AclLab-'+[guid]::NewGuid().ToString('N'))
$root=Join-Path $parent 'candidate'
$app=Join-Path $root 'app'
$state=Join-Path $root 'state'
$journal=Join-Path $state 'journal'
$count=0
$user=$null
function Pass([string]$Label){$script:count++;Write-Output ('ACL_TEST='+$Label+'; PASS')}
function Base-Acl {
    $acl=[Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($adminSid);$acl.SetAccessRuleProtection($true,$false)
    foreach($sid in @($adminSid,$systemSid)){
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,[Security.AccessControl.FileSystemRights]::FullControl,$inherit,$none,$allow))
    }
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($usersSid,[Security.AccessControl.FileSystemRights]::ReadAndExecute,$inherit,$none,$allow))
    return $acl
}
function Set-FixtureAcl([string]$Path,[Security.AccessControl.DirectorySecurity]$Acl){
    ([IO.DirectoryInfo]::new($Path)).SetAccessControl($Acl)
}
function Expect-Blocked([scriptblock]$Action,[string]$Code){
    $actual=''
    try{& $Action}catch{$actual=$_.Exception.Message}
    Require ($actual -ceq $Code) ('EXPECTED_'+$Code+'_ACTUAL_'+$actual)
}
function Check-Both([string]$Path,[bool]$Rejected,[string]$Label){
    if($Rejected){
        Expect-Blocked {Assert-Ancestors $Path} 'ANCESTOR_ACL'
        $want='UNSAFE_ANCESTOR_ACL'
    }else{
        Assert-Ancestors $Path
        $want='PASS'
    }
    & $TestExe acl-parent $Path $want
    Require ($LASTEXITCODE -eq 0) 'RUNTIME_GUARD_TEST_FAILED'
    Pass $Label
}
function Grant([Security.AccessControl.DirectorySecurity]$Acl,$Sid,[Security.AccessControl.FileSystemRights]$Rights,[Security.AccessControl.InheritanceFlags]$Inherit,[Security.AccessControl.PropagationFlags]$Propagation){
    $Acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($Sid,$Rights,$Inherit,$Propagation,$allow))
}
try{
    # Real non-administrator local user, used only as an ACL principal on
    # this disposable runner. Its synthetic password is never used/logged.
    $name='identacl'+[guid]::NewGuid().ToString('N').Substring(0,10)
    $pw=ConvertTo-SecureString ('Aa9!'+[guid]::NewGuid().ToString('N')) -AsPlainText -Force
    try{$user=New-LocalUser -Name $name -Password $pw -AccountNeverExpires -UserMayNotChangePassword -Disabled}
    finally{$pw.Dispose()}
    $untrusted=$user.SID
    Require ($untrusted -ne $adminSid -and $untrusted -ne $systemSid -and $untrusted -ne $serviceSid) 'UNTRUSTED_SID'
    [void][IO.Directory]::CreateDirectory($parent,(Base-Acl))
    Check-Both $parent $false 'BASE_READ_ONLY'
    foreach($case in @(@('CREATE_FILES',[Security.AccessControl.FileSystemRights]::CreateFiles),@('CREATE_DIRECTORIES',[Security.AccessControl.FileSystemRights]::CreateDirectories))){
        $label=[string]$case[0];$right=$case[1]
        $acl=Base-Acl
        Grant $acl $untrusted $right ([Security.AccessControl.InheritanceFlags]::None) $none
        Set-FixtureAcl $parent $acl
        $actual=([IO.DirectoryInfo]::new($parent)).GetAccessControl()
        $rules=@($actual.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])|Where-Object {$_.IdentityReference -eq $untrusted -and $_.AccessControlType -eq 'Allow' -and ($_.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -eq 0 -and ($_.FileSystemRights -band $right) -ne 0})
        Require ($rules.Count -eq 1) 'FIXTURE_GRANT_NOT_EFFECTIVE'
        Check-Both $parent $true ('DIRECT_'+$label)
        Expect-Blocked {New-PrivateDirectory $root $false} 'ANCESTOR_ACL'
        Require (-not (Test-Path -LiteralPath $root)) 'UNSAFE_PARENT_CREATED_TARGET'
        Pass ('NO_CREATE_UNDER_'+$label)
        # A clean immediate parent is not sufficient: reject a higher
        # ancestor whose direct create ACE does not inherit to its children.
        $inner=Join-Path $parent 'inner'
        [void][IO.Directory]::CreateDirectory($inner,(Base-Acl))
        Check-Both $inner $true ('HIGHER_ANCESTOR_'+$label)
        [IO.Directory]::Delete($inner)
        # An inherit-only ACE is not effective on its own parent. On an
        # unprotected child that receives it, the effective inherited ACE
        # must be rejected, regardless of IsInherited.
        $acl=Base-Acl
        Grant $acl $untrusted $right $inherit ([Security.AccessControl.PropagationFlags]::InheritOnly)
        Set-FixtureAcl $parent $acl
        Check-Both $parent $false ('INHERIT_ONLY_PARENT_'+$label)
        [void][IO.Directory]::CreateDirectory($inner)
        $got=([IO.DirectoryInfo]::new($inner)).GetAccessControl()
        $inherited=@($got.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])|Where-Object {$_.IdentityReference -eq $untrusted -and $_.IsInherited -and ($_.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -eq 0 -and ($_.FileSystemRights -band $right) -ne 0})
        Require ($inherited.Count -ge 1) 'INHERITED_FIXTURE_MISSING'
        # Set a trusted owner while retaining inherited permissions, so the
        # failure being tested is the access mask rather than ownership.
        $got.SetOwner($adminSid);Set-FixtureAcl $inner $got
        Check-Both $inner $true ('INHERITED_'+$label)
        [IO.Directory]::Delete($inner)
        Set-FixtureAcl $parent (Base-Acl)
    }
    # Poison only child inheritance. The parent itself has no untrusted
    # create rights. The kernel creation must NOT inherit the poisonous ACE.
    $acl=Base-Acl
    Grant $acl $untrusted ([Security.AccessControl.FileSystemRights]::FullControl) $inherit ([Security.AccessControl.PropagationFlags]::InheritOnly)
    Set-FixtureAcl $parent $acl
    Check-Both $parent $false 'POISON_INHERIT_ONLY_PARENT'
    New-PrivateDirectory $root $false
    New-PrivateDirectory $app $false
    New-PrivateDirectory $state $true
    New-PrivateDirectory $journal $true
    Assert-InstalledDirectoryLayout
    Check-Both $root $false 'ATOMIC_PROTECTED_ROOT'
    foreach($p in @($root,$app,$state,$journal)){
        $acl=([IO.DirectoryInfo]::new($p)).GetAccessControl()
        Require ($acl.AreAccessRulesProtected -and @($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])|Where-Object {$_.IsInherited -or $_.IdentityReference -eq $untrusted -or $_.IdentityReference -eq $usersSid}).Count -eq 0) 'ATOMIC_DACL_INHERITED'
    }
    Pass 'ALL_FOUR_PROTECTED_AT_CREATION'
    $sub=Join-Path $app 'nested'
    New-PrivateDirectory $sub $false
    Assert-CreatedDirectory $sub $false
    Pass 'NESTED_PROTECTED_DIRECTORY'
    $before=([IO.DirectoryInfo]::new($sub)).GetAccessControl().GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::All -band (-bnot [Security.AccessControl.AccessControlSections]::Audit))
    $marker=Join-Path $sub 'keep.txt'
    [IO.File]::WriteAllText($marker,'do-not-adopt')
    Expect-Blocked {New-PrivateDirectory $sub $false} 'DIRECTORY_EXISTS'
    $after=([IO.DirectoryInfo]::new($sub)).GetAccessControl().GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::All -band (-bnot [Security.AccessControl.AccessControlSections]::Audit))
    Require ($before -ceq $after -and [IO.File]::ReadAllText($marker) -ceq 'do-not-adopt') 'EXISTING_DIRECTORY_CHANGED'
    Pass 'EXISTING_DIRECTORY_NO_ADOPTION'
    $file=Join-Path $app 'occupied'
    [IO.File]::WriteAllText($file,'occupied-file')
    Expect-Blocked {New-PrivateDirectory $file $false} 'DIRECTORY_EXISTS'
    Require ([IO.File]::ReadAllText($file) -ceq 'occupied-file') 'EXISTING_FILE_CHANGED'
    Pass 'EXISTING_FILE_NO_ADOPTION'
    $missing=Join-Path $app 'missing'
    $failed=$false
    try{New-PrivateDirectory (Join-Path $missing 'child') $false}catch{$failed=$true}
    Require ($failed -and -not (Test-Path -LiteralPath $missing)) 'IMPLICIT_PARENT_CREATED'
    Pass 'NO_IMPLICIT_PARENT_CREATION'
    # A changed internal parent must not be silently repaired or adopted.
    $appAcl=([IO.DirectoryInfo]::new($app)).GetAccessControl()
    $bad=([IO.DirectoryInfo]::new($app)).GetAccessControl()
    Grant $bad $untrusted ([Security.AccessControl.FileSystemRights]::CreateFiles) ([Security.AccessControl.InheritanceFlags]::None) $none
    Set-FixtureAcl $app $bad
    Expect-Blocked {New-PrivateDirectory (Join-Path $app 'must-not-create') $false} 'PRIVATE_DIRECTORY_ACE_COUNT'
    Require (-not (Test-Path -LiteralPath (Join-Path $app 'must-not-create'))) 'TAMPERED_PARENT_CREATED_TARGET'
    Set-FixtureAcl $app $appAcl
    Pass 'TAMPERED_INTERNAL_PARENT_REJECTED'
    & $TestExe acl-layout
    Require ($LASTEXITCODE -eq 0) 'REAL_STAGED_LAYOUT_FAILED'
    Pass 'REAL_STAGED_LAYOUT'
    Write-Output ('IDENT_006C_01_WINDOWS_ACL='+$count+'/'+$count+' PASS')
}finally{
    if(Test-Path -LiteralPath $parent){Remove-Item -LiteralPath $parent -Recurse -Force}
    if($null -ne $user){Remove-LocalUser -SID $user.SID}
}
