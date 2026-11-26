# Run only on Windows PowerShell 5.1, with synthetic ACLs and no SQL connection.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'outbox_guard.ps1')
$sid = Get-PrivateOutboxIdentity
$everyone = [System.Security.Principal.SecurityIdentifier]::new('S-1-1-0')
function Assert-Throws([scriptblock]$Action, [string]$Expected) {
    $caught = $false
    try { & $Action } catch { if ($_.Exception.Message -ne $Expected) { throw }; $caught = $true }
    if (-not $caught) { throw ('EXPECTED_REJECTION_' + $Expected) }
}
function New-TestAcl {
    $acl = [System.Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($sid)
    $acl.SetAccessRuleProtection($true,$false)
    $flags = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new($sid,[System.Security.AccessControl.FileSystemRights]::FullControl,$flags,[System.Security.AccessControl.PropagationFlags]::None,[System.Security.AccessControl.AccessControlType]::Allow)
    $acl.AddAccessRule($rule)
    return $acl
}
Assert-PrivateAcl (New-TestAcl) $sid $true
$acl = New-TestAcl
$acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($everyone,[System.Security.AccessControl.FileSystemRights]::Read,[System.Security.AccessControl.AccessControlType]::Allow))
Assert-Throws { Assert-PrivateAcl $acl $sid $true } 'OUTBOX_ACL_PRINCIPAL'
$acl = New-TestAcl
$acl.SetAccessRuleProtection($false,$false)
Assert-Throws { Assert-PrivateAcl $acl $sid $true } 'OUTBOX_ACL_INHERITANCE'
$acl = New-TestAcl
$acl.SetOwner($everyone)
Assert-Throws { Assert-PrivateAcl $acl $sid $true } 'OUTBOX_OWNER'
Assert-PrivateAcl (New-PrivateFileSecurity $sid) $sid $false
Assert-Throws { Assert-PrivateOutbox '\\server\share' $sid } 'OUTBOX_PATH'
Assert-Throws { Assert-PrivateOutbox 'C:\temp\..\folder' $sid } 'OUTBOX_PATH'
Assert-OutboxNotShared @([pscustomobject]@{Name='C$';Path='C:\';Type=[uint32]2147483648})
Assert-Throws { Assert-OutboxNotShared @([pscustomobject]@{Name='unrelated';Path='C:\private-other';Type=0}) } 'OUTBOX_SHARED_PATH'
Assert-Throws { Assert-OutboxNotShared @([pscustomobject]@{Name='custom';Path='C:\private';Type=0}) } 'OUTBOX_SHARED_PATH'
Assert-Throws { Assert-OutboxNotShared @([pscustomobject]@{Name='custom';Path='C:\private\outbox\sub';Type=0}) } 'OUTBOX_SHARED_PATH'
# Verify actual FileStream security overload with synthetic bytes only.
$filePath = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString('D') + '.ident004-test')
$file = [System.IO.FileStream]::new($filePath,[System.IO.FileMode]::CreateNew,[System.Security.AccessControl.FileSystemRights]::FullControl,[System.IO.FileShare]::None,4096,[System.IO.FileOptions]::WriteThrough,(New-PrivateFileSecurity $sid))
try {
    Assert-PrivateAcl ($file.GetAccessControl()) $sid $false
    $file.WriteByte(42); $file.Flush($true)
} finally { $file.Dispose(); [System.IO.File]::Delete($filePath) }
Write-Output 'OUTBOX_SYNTHETIC_TESTS=PASS'
