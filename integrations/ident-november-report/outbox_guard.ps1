# Windows PowerShell 5.1 / NTFS. Validation only, never changes existing ACLs or shares.
function Get-PrivateOutboxIdentity {
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    if ($null -eq $sid) { throw 'OUTBOX_IDENTITY' }
    return $sid
}

function Assert-PrivateAcl($Acl, $Sid, [bool]$Directory) {
    $allowed = @($Sid.Value, 'S-1-5-18', 'S-1-5-32-544')
    $full = [int][System.Security.AccessControl.FileSystemRights]::FullControl
    if (-not $Acl.AreAccessRulesProtected -or -not $Acl.AreAccessRulesCanonical) { throw 'OUTBOX_ACL_INHERITANCE' }
    if ($Acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -notin $allowed) { throw 'OUTBOX_OWNER' }
    $ownFull = $false
    foreach ($rule in $Acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier])) {
        if ($rule.IsInherited -or $rule.IdentityReference.Value -notin $allowed -or $rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) { throw 'OUTBOX_ACL_PRINCIPAL' }
        if ($rule.IdentityReference.Value -eq $Sid.Value -and (([int]$rule.FileSystemRights -band $full) -eq $full) -and $rule.PropagationFlags -eq [System.Security.AccessControl.PropagationFlags]::None) {
            if (-not $Directory -or ($rule.InheritanceFlags -eq ([System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit))) { $ownFull = $true }
        }
    }
    if (-not $ownFull) { throw 'OUTBOX_ACL_CURRENT_USER' }
}

function Test-PathWithin([string]$Path, [string]$Root) {
    $p = $Path.TrimEnd('\'); $r = $Root.TrimEnd('\')
    return $p.Equals($r,[StringComparison]::OrdinalIgnoreCase) -or $p.StartsWith($r + '\',[StringComparison]::OrdinalIgnoreCase)
}

function Assert-PrivateOutbox([string]$Path, $Sid) {
    if ($PSVersionTable.PSEdition -ne 'Desktop') { throw 'OUTBOX_WINDOWS_FRAMEWORK_REQUIRED' }
    if ($Path -notmatch '^[A-Za-z]:\\' -or $Path.Substring(2) -match '[:/]|(?:^|\\)[^\\]*[. ](?:\\|$)|\\{2,}') { throw 'OUTBOX_PATH' }
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    $local = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    if ([string]::IsNullOrWhiteSpace($local)) { throw 'OUTBOX_PROFILE' }
    $root = [System.IO.Path]::GetFullPath((Join-Path $local 'IDENT-Bridge\outbox'))
    if (-not (Test-PathWithin $full $root)) { throw 'OUTBOX_LOCATION' }
    $drive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($full))
    if ($drive.DriveType -ne [System.IO.DriveType]::Fixed -or $drive.DriveFormat -ne 'NTFS') { throw 'OUTBOX_LOCAL_NTFS_REQUIRED' }
    if (-not [System.IO.Directory]::Exists($full)) { throw 'OUTBOX_MUST_ALREADY_EXIST' }
    # TrustedInstaller is a built-in OS servicing identity and may own system ancestors.
    $allowed = @($Sid.Value, 'S-1-5-18', 'S-1-5-32-544', 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    $danger = [int]([System.Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor [System.Security.AccessControl.FileSystemRights]::Delete -bor [System.Security.AccessControl.FileSystemRights]::ChangePermissions -bor [System.Security.AccessControl.FileSystemRights]::TakeOwnership -bor [System.Security.AccessControl.FileSystemRights]::WriteAttributes -bor [System.Security.AccessControl.FileSystemRights]::WriteExtendedAttributes) -bor 0x50000000
    $dir = [System.IO.DirectoryInfo]::new($full)
    Assert-PrivateAcl ($dir.GetAccessControl()) $Sid $true
    while ($null -ne $dir) {
        if (($dir.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'OUTBOX_REPARSE_POINT' }
        $acl = $dir.GetAccessControl()
        if ($acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -notin $allowed) { throw 'OUTBOX_ANCESTOR_OWNER' }
        foreach ($rule in $acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier])) {
            if ($rule.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and $rule.IdentityReference.Value -notin $allowed -and ($rule.PropagationFlags -band [System.Security.AccessControl.PropagationFlags]::InheritOnly) -eq 0 -and (([int]$rule.FileSystemRights -band $danger) -ne 0)) { throw 'OUTBOX_ANCESTOR_ACL' }
        }
        $dir = $dir.Parent
    }
    # Pilot: require no ordinary disk shares on the collector host. Lexical path
    # comparisons alone cannot exclude aliases or junctions inside another share.
    try { $shares = @(Get-CimInstance -ClassName Win32_Share -Namespace root/cimv2 -ErrorAction Stop) } catch { throw 'OUTBOX_SHARE_CHECK_FAILED' }
    Assert-OutboxNotShared $shares
    return $full
}

function Assert-OutboxNotShared($Shares) {
    foreach ($share in $Shares) {
        if (([uint32]$share.Type -band 65535) -ne 0) { continue }
        if ([string]::IsNullOrWhiteSpace($share.Path)) { throw 'OUTBOX_SHARE_CHECK_FAILED' }
        $shared = [System.IO.Path]::GetFullPath($share.Path).TrimEnd('\')
        $driveAdmin = ([string]$share.Name -match '^[A-Za-z]\$$') -and ($shared -ieq ([string]$share.Name).Replace('$',':'))
        $windowsAdmin = ([string]$share.Name -ieq 'ADMIN$') -and ($shared -ieq [Environment]::GetFolderPath([Environment+SpecialFolder]::Windows).TrimEnd('\'))
        if ([uint32]$share.Type -eq 2147483648 -and ($driveAdmin -or $windowsAdmin)) { continue }
        throw 'OUTBOX_SHARED_PATH'
    }
}

function New-PrivateFileSecurity($Sid) {
    $security = [System.Security.AccessControl.FileSecurity]::new()
    $security.SetOwner($Sid)
    $security.SetAccessRuleProtection($true,$false)
    foreach ($value in @($Sid.Value, 'S-1-5-18', 'S-1-5-32-544') | Select-Object -Unique) {
        $identity = [System.Security.Principal.SecurityIdentifier]::new($value)
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new($identity,[System.Security.AccessControl.FileSystemRights]::FullControl,[System.Security.AccessControl.AccessControlType]::Allow)
        $security.AddAccessRule($rule)
    }
    return $security
}
