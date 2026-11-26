"""Disposable GitHub-hosted Windows runner ONLY. Nonpropagating DACL setter.
No production deployment. The caller supplies a previously verified raw
security descriptor with only direct create-only grants removed, or its backup.
"""
import ctypes as ct
from ctypes import wintypes as wt
import os
from pathlib import Path
import sys

if (os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true"
    or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    or os.environ.get("RUNNER_OS") != "Windows"
    or os.environ.get("GITHUB_REF") != "refs/heads/test/ident-006c-sender-20261126"):
    raise SystemExit("DISPOSABLE_RUNNER_REQUIRED")
if len(sys.argv) != 2:
    raise SystemExit("DESCRIPTOR_REQUIRED")
path = Path(sys.argv[1]).resolve(strict=True)
lab = (Path(os.environ["RUNNER_TEMP"])/"ident006c-lab").resolve(strict=True)
if path.parent != lab or path.name not in ("candidate-root-security.bin", "original-root-security.bin"):
    raise SystemExit("LAB_DESCRIPTOR_PATH")
root = os.environ.get("SystemDrive", "") + "\\"
if root.upper() != "C:\\":
    raise SystemExit("EXPECTED_DISPOSABLE_SYSTEM_VOLUME")
data = path.read_bytes()
if not 20 <= len(data) <= 65536:
    raise SystemExit("DESCRIPTOR_SIZE")
advapi = ct.WinDLL("advapi32.dll", use_last_error=True)
set_security = advapi.SetFileSecurityW
set_security.argtypes = [wt.LPCWSTR, wt.DWORD, ct.c_void_p]
set_security.restype = wt.BOOL
valid = advapi.IsValidSecurityDescriptor
valid.argtypes = [ct.c_void_p]
valid.restype = wt.BOOL
buf = ct.create_string_buffer(data)
if not valid(buf):
    raise SystemExit("INVALID_DESCRIPTOR")
# DACL_SECURITY_INFORMATION only. No recursive propagation and no owner/SACL change.
if not set_security(root, 4, buf):
    raise SystemExit("NATIVE_DACL_ERROR="+str(ct.get_last_error()))
print("NATIVE_DACL_WRITE=PASS; SCOPE=DISPOSABLE_ROOT_ONLY", flush=True)
