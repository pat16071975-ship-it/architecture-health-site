using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using Microsoft.Win32.SafeHandles;

internal static class PrivateStore
{
    internal const string ServiceName="AzIdent006C";
    internal static string Root => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),"AZ-IDENT006C");
    internal static string App => Path.Combine(Root,"app");
    internal static string State => Path.Combine(Root,"state");
    internal static string JournalDir => Path.Combine(State,"journal");
    internal static string ConfigPath => Path.Combine(App,"config.json");
    internal static string Pin => Path.Combine(App,"server.cer");
    internal static string SqlSecret => Path.Combine(App,"sql.dpapi");
    internal static string TokenSecret => Path.Combine(App,"token.dpapi");
    internal static SecurityIdentifier ServiceSid => (SecurityIdentifier)new NTAccount("NT SERVICE",ServiceName).Translate(typeof(SecurityIdentifier));
    internal static readonly SecurityIdentifier Admin=new(WellKnownSidType.BuiltinAdministratorsSid,null);
    internal static readonly SecurityIdentifier SystemSid=new(WellKnownSidType.LocalSystemSid,null);
    private static readonly string TrustedInstaller="S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464";

    internal static void AssertAdmin()
    {
        using var identity=WindowsIdentity.GetCurrent();
        Guard.Require(new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator),"ADMIN_REQUIRED");
    }
    internal static void AssertService()
    {
        using var identity=WindowsIdentity.GetCurrent();
        Guard.Require(identity.User==ServiceSid && !new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator),"SERVICE_IDENTITY_REQUIRED");
    }
    private static bool Trusted(string sid) => sid==Admin.Value || sid==SystemSid.Value || sid==ServiceSid.Value;
    private static bool OwnerTrusted(string sid) => Trusted(sid) || sid==TrustedInstaller;

    internal static void AssertAncestors(string path)
    {
        Guard.Require(Path.IsPathFullyQualified(path) && Path.GetFullPath(path)==path,"LOCAL_PATH_REQUIRED");
        var drive=new DriveInfo(Path.GetPathRoot(path)!);
        Guard.Require(drive.DriveType==DriveType.Fixed && drive.DriveFormat=="NTFS","LOCAL_NTFS_REQUIRED");
        for (DirectoryInfo? dir=new DirectoryInfo(path);dir!=null;dir=dir.Parent)
        {
            Guard.Require(dir.Exists && (dir.Attributes & FileAttributes.ReparsePoint)==0,"DIRECTORY_REPARSE_OR_MISSING");
            var acl=dir.GetAccessControl(AccessControlSections.Access|AccessControlSections.Owner);
            Guard.Require(acl.AreAccessRulesCanonical && OwnerTrusted(acl.GetOwner(typeof(SecurityIdentifier)).Value),"UNSAFE_ANCESTOR_OWNER");
            const FileSystemRights danger=FileSystemRights.DeleteSubdirectoriesAndFiles|FileSystemRights.Delete|FileSystemRights.ChangePermissions|FileSystemRights.TakeOwnership|FileSystemRights.WriteAttributes|FileSystemRights.WriteExtendedAttributes;
            foreach(FileSystemAccessRule rule in acl.GetAccessRules(true,true,typeof(SecurityIdentifier)))
                if(rule.AccessControlType==AccessControlType.Allow && !OwnerTrusted(rule.IdentityReference.Value) && (rule.PropagationFlags&PropagationFlags.InheritOnly)==0)
                    Guard.Require((rule.FileSystemRights&danger)==0 && ((int)rule.FileSystemRights&0x50000000)==0,"UNSAFE_ANCESTOR_ACL");
        }
    }

    internal static void AssertAcl(FileSystemInfo info,bool writable)
    {
        Guard.Require(info.Exists && (info.Attributes&FileAttributes.ReparsePoint)==0,"PRIVATE_PATH_INVALID");
        var acl=info is DirectoryInfo d ? (FileSystemSecurity)d.GetAccessControl(AccessControlSections.Access|AccessControlSections.Owner)
                                         : ((FileInfo)info).GetAccessControl(AccessControlSections.Access|AccessControlSections.Owner);
        string owner=acl.GetOwner(typeof(SecurityIdentifier)).Value;
        Guard.Require(acl.AreAccessRulesCanonical && Trusted(owner) && (writable || owner!=ServiceSid.Value),"PRIVATE_OWNER");
        const FileSystemRights write=FileSystemRights.WriteData|FileSystemRights.AppendData|FileSystemRights.Delete|FileSystemRights.DeleteSubdirectoriesAndFiles|FileSystemRights.WriteAttributes|FileSystemRights.WriteExtendedAttributes|FileSystemRights.ChangePermissions|FileSystemRights.TakeOwnership;
        bool hasService=false;
        foreach(FileSystemAccessRule r in acl.GetAccessRules(true,true,typeof(SecurityIdentifier)))
        {
            Guard.Require(r.AccessControlType==AccessControlType.Allow && Trusted(r.IdentityReference.Value),"PRIVATE_ACL_PRINCIPAL");
            if(r.IdentityReference.Value==ServiceSid.Value)
            {
                if(!writable) Guard.Require((r.FileSystemRights&write)==0 && ((int)r.FileSystemRights&0x50000000)==0,"SERVICE_WRITE_TO_APP");
                if((r.PropagationFlags&PropagationFlags.InheritOnly)==0 && (r.FileSystemRights&FileSystemRights.ReadData)!=0) hasService=true;
            }
        }
        Guard.Require(hasService,"SERVICE_ACL_REQUIRED");
    }

    internal static void VerifyLayout()
    {
        AssertAncestors(Root);
        foreach(string p in new[]{Root,App}) AssertAcl(new DirectoryInfo(p),false);
        foreach(string p in new[]{Root,App,State}) Guard.Require(new DirectoryInfo(p).GetAccessControl(AccessControlSections.Access).AreAccessRulesProtected,"PRIVATE_DIRECTORY_INHERITANCE");
        foreach(string p in new[]{State,JournalDir}) AssertAcl(new DirectoryInfo(p),true);
        foreach(string p in new[]{ConfigPath,Pin,SqlSecret,TokenSecret}) AssertAcl(new FileInfo(p),false);
        AssertNoOrdinaryShares();
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ShareInfo { public IntPtr Name; public uint Type; public IntPtr Remark; }
    [DllImport("Netapi32.dll",CharSet=CharSet.Unicode)]
    private static extern uint NetShareEnum(string? server,uint level,out IntPtr buffer,uint prefmaxlen,out uint entriesread,out uint totalentries,ref uint resume);
    [DllImport("Netapi32.dll")] private static extern uint NetApiBufferFree(IntPtr buffer);
    internal static void AssertNoOrdinaryShares()
    {
        uint resume=0; IntPtr buffer=IntPtr.Zero;
        try
        {
            uint rc=NetShareEnum(null,1,out buffer,uint.MaxValue,out uint count,out _,ref resume);
            Guard.Require(rc==0 && count<=4096,"SHARE_CHECK_FAILED");
            int size=Marshal.SizeOf<ShareInfo>();
            for(int i=0;i<count;i++)
            {
                var share=Marshal.PtrToStructure<ShareInfo>(IntPtr.Add(buffer,checked(i*size)));
                if((share.Type&0xffff)!=0) continue;
                string name=Marshal.PtrToStringUni(share.Name)??"";
                Guard.Require(share.Type==0x80000000 && (name.Equals("ADMIN$",StringComparison.OrdinalIgnoreCase) ||
                    (name.Length==2 && char.IsAsciiLetter(name[0]) && name[1]=='$')),"ORDINARY_DISK_SHARE");
            }
        }
        finally { if(buffer!=IntPtr.Zero) NetApiBufferFree(buffer); }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct FileInfoNative
    {
        public uint Attributes,CreationLow,CreationHigh,AccessLow,AccessHigh,WriteLow,WriteHigh,VolumeSerial,SizeHigh,SizeLow,Links,IndexHigh,IndexLow;
    }
    [DllImport("kernel32.dll",SetLastError=true)]
    private static extern bool GetFileInformationByHandle(SafeFileHandle handle,out FileInfoNative info);
    internal static byte[] Read(string path,int limit,bool writable)
    {
        AssertAcl(new FileInfo(path),writable);
        using var f=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read);
        Guard.Require(GetFileInformationByHandle(f.SafeFileHandle,out var info) && info.Links==1 && (info.Attributes&(uint)FileAttributes.ReparsePoint)==0,"PRIVATE_FILE_LINK");
        Guard.Require(f.Length>0 && f.Length<=limit,"PRIVATE_FILE_SIZE");
        var bytes=new byte[(int)f.Length];f.ReadExactly(bytes);return bytes;
    }

    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true,ExactSpelling=true)]
    private static extern bool MoveFileExW(string existing,string target,uint flags);
    // MOVEFILE_WRITE_THROUGH, no replacement. Target and candidate are on the same volume.
    internal static void Publish(string path,byte[] bytes,bool replace=false,bool adminOwner=false)
    {
        string tmp=Path.Combine(Path.GetDirectoryName(path)!,".tmp-"+Guid.NewGuid().ToString("N"));
        try
        {
            using(var f=new FileStream(tmp,FileMode.CreateNew,FileAccess.Write,FileShare.None,4096,FileOptions.WriteThrough))
            { f.Write(bytes); f.Flush(true); }
            if(adminOwner)
            {
                var info=new FileInfo(tmp);var acl=info.GetAccessControl(AccessControlSections.Owner);acl.SetOwner(Admin);info.SetAccessControl(acl);
            }
            Guard.Require(MoveFileExW(tmp,path,8u|(replace?1u:0u)),"ATOMIC_PUBLISH_FAILED");
        }
        finally { if(File.Exists(tmp)) File.Delete(tmp); }
    }

    internal static FileStream OpenLock(string path)
    {
        var file=new FileStream(path,FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None);
        try
        {
            Guard.Require(GetFileInformationByHandle(file.SafeFileHandle,out var info)&&info.Links==1&&(info.Attributes&(uint)FileAttributes.ReparsePoint)==0,"LOCK_FILE_LINK");
            return file;
        }
        catch{file.Dispose();throw;}
    }

    internal static byte[] ReadInput(string path,int limit)
    {
        Guard.Require(Path.IsPathFullyQualified(path) && Path.GetFullPath(path)==path,"INPUT_PATH");
        var info=new FileInfo(path);
        Guard.Require(info.Exists && (info.Attributes&FileAttributes.ReparsePoint)==0,"INPUT_FILE");
        using var f=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read);
        Guard.Require(GetFileInformationByHandle(f.SafeFileHandle,out var details)&&details.Links==1&&(details.Attributes&(uint)FileAttributes.ReparsePoint)==0,"INPUT_LINK");
        Guard.Require(f.Length>0&&f.Length<=limit,"INPUT_SIZE");
        byte[] raw=new byte[(int)f.Length];f.ReadExactly(raw);return raw;
    }
}

internal static class Dpapi
{
    [StructLayout(LayoutKind.Sequential)] private struct Blob { public int Size;public IntPtr Data; }
    [DllImport("crypt32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
    private static extern bool CryptProtectData(ref Blob input,string? description,ref Blob entropy,IntPtr reserved,IntPtr prompt,uint flags,out Blob output);
    [DllImport("crypt32.dll",SetLastError=true)]
    private static extern bool CryptUnprotectData(ref Blob input,IntPtr description,ref Blob entropy,IntPtr reserved,IntPtr prompt,uint flags,out Blob output);
    [DllImport("kernel32.dll")] private static extern IntPtr LocalFree(IntPtr p);
    private static Blob Allocate(byte[] bytes)
    {
        var blob=new Blob {Size=bytes.Length,Data=Marshal.AllocHGlobal(bytes.Length)};
        Marshal.Copy(bytes,0,blob.Data,bytes.Length);return blob;
    }
    private static void Wipe(Blob b,bool local)
    {
        if(b.Data==IntPtr.Zero)return;
        for(int i=0;i<b.Size;i++) Marshal.WriteByte(b.Data,i,0);
        if(local)LocalFree(b.Data);else Marshal.FreeHGlobal(b.Data);
    }
    internal static byte[] Transform(byte[] bytes,byte[] binding,string purpose,bool protect)
    {
        Guard.Require(bytes.Length>0&&bytes.Length<=PrivateStoreLimit,"DPAPI_SIZE");
        byte[] purposeBytes=Encoding.UTF8.GetBytes("AZ-IDENT006C/"+purpose);
        byte[] combined=new byte[binding.Length+purposeBytes.Length];
        binding.CopyTo(combined,0);purposeBytes.CopyTo(combined,binding.Length);
        byte[] entropy=SHA256.HashData(combined);
        Blob input=default,extra=default,output=default;
        try
        {
            input=Allocate(bytes);extra=Allocate(entropy);
            // Machine scope intentionally permits the admin provisioner and the
            // dedicated virtual service account to share this ciphertext.
            // Windows ACLs restrict who can obtain it. No UI, no plaintext file.
            bool ok=protect?CryptProtectData(ref input,null,ref extra,IntPtr.Zero,IntPtr.Zero,5,out output):
                CryptUnprotectData(ref input,IntPtr.Zero,ref extra,IntPtr.Zero,IntPtr.Zero,1,out output);
            Guard.Require(ok && output.Size>0&&output.Size<=PrivateStoreLimit,"DPAPI_FAILED");
            var result=new byte[output.Size];Marshal.Copy(output.Data,result,0,output.Size);return result;
        }
        finally {Wipe(input,false);Wipe(extra,false);Wipe(output,true);CryptographicOperations.ZeroMemory(entropy);}
    }
    internal const int PrivateStoreLimit=Guard.MaxPayload+65536;

    internal static SecureString ConsoleSecret()
    {
        Guard.Require(!Console.IsInputRedirected,"INTERACTIVE_CONSOLE_REQUIRED");
        var secret=new SecureString();
        try
        {
            while(true)
            {
                var key=Console.ReadKey(true);
                if(key.Key==ConsoleKey.Enter)break;
                if(key.Key==ConsoleKey.Escape)throw new Blocked("PASSWORD_CANCELLED");
                if(key.Key==ConsoleKey.Backspace){if(secret.Length>0)secret.RemoveAt(secret.Length-1);continue;}
                if(char.IsControl(key.KeyChar))continue;
                Guard.Require(secret.Length<128,"PASSWORD_TOO_LONG");secret.AppendChar(key.KeyChar);
            }
            Console.Error.WriteLine();Guard.Require(secret.Length>0,"PASSWORD_REQUIRED");secret.MakeReadOnly();return secret;
        }
        catch{secret.Dispose();throw;}
    }
    internal static byte[] PasswordBytes(SecureString secret)
    {
        IntPtr p=Marshal.SecureStringToGlobalAllocUnicode(secret);
        try{var raw=new byte[secret.Length*2];Marshal.Copy(p,raw,0,raw.Length);return raw;}
        finally{Marshal.ZeroFreeGlobalAllocUnicode(p);}
    }
    internal static SecureString ReadPassword(Settings cfg)
    {
        byte[] raw=Transform(PrivateStore.Read(PrivateStore.SqlSecret,PrivateStoreLimit,false),cfg.Binding,"sql-password",false);
        var password=new SecureString();
        try
        {
            Guard.Require(raw.Length is >=2 and <=256 && raw.Length%2==0,"PASSWORD_FORMAT");
            for(int i=0;i<raw.Length;i+=2){char c=(char)(raw[i]|raw[i+1]<<8);Guard.Require(!char.IsControl(c),"PASSWORD_FORMAT");password.AppendChar(c);}
            password.MakeReadOnly();return password;
        }
        catch{password.Dispose();throw;}
        finally{CryptographicOperations.ZeroMemory(raw);}
    }
    internal static byte[] ReadToken(Settings cfg)
    {
        byte[] raw=Transform(PrivateStore.Read(PrivateStore.TokenSecret,PrivateStoreLimit,false),cfg.Binding,"http-token",false);
        if(raw.Length!=64 || !raw.All(x=>x is >=(byte)'0' and <=(byte)'9' or >=(byte)'a' and <=(byte)'f') ||
           !CryptographicOperations.FixedTimeEquals(SHA256.HashData(raw),Convert.FromHexString(cfg.TokenHash)))
        {CryptographicOperations.ZeroMemory(raw);throw new Blocked("TOKEN_BINDING");}
        return raw;
    }
}
