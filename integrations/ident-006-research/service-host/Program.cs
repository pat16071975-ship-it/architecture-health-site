// Separate Windows SCM host for IDENT-006D. Does not change IDENT or its database.
// Runtime API key: administrator-provisioned, DPAPI, Windows ACL, never argv/logs.
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

internal static class HostProgram
{
    const string ServiceName = "AzIdentResearch";
    const int OwnProcess=0x10, Stopped=1, StartPending=2, StopPending=3, Running=4;
    const int AcceptStop=1, AcceptShutdown=4, ControlStop=1, ControlShutdown=5, ErrorServiceSpecific=1066;
    static readonly ManualResetEventSlim StopRequested=new(false);
    static readonly ServiceMainCallback MainCallbackRef=ServiceMain;
    static readonly ControlCallback ControlCallbackRef=OnControl;
    static IntPtr serviceHandle;
    static int checkpoint, exitCode;

    static string RuntimeFile => Path.Combine(PrivateStore.App,"tunnel-runtime.json");
    static string RuntimeSecret => Path.Combine(PrivateStore.App,"tunnel-runtime.dpapi");
    static string BridgeExe => Path.Combine(PrivateStore.App,"bridge","AzIdentResearch.exe");
    static string TunnelExe => Path.Combine(PrivateStore.App,"tunnel","tunnel-client.exe");
    static string HostExe => Path.Combine(PrivateStore.App,"host","AzIdentResearchHost.exe");

    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]
    struct ServiceTableEntry
    {
        [MarshalAs(UnmanagedType.LPWStr)] public string? Name;
        public ServiceMainCallback? Callback;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct ServiceStatus
    {
        public int ServiceType, CurrentState, ControlsAccepted, Win32ExitCode;
        public int ServiceSpecificExitCode, Checkpoint, WaitHint;
    }
    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    delegate void ServiceMainCallback(int argc,IntPtr argv);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    delegate int ControlCallback(int control,int eventType,IntPtr eventData,IntPtr context);
    [DllImport("advapi32.dll",EntryPoint="StartServiceCtrlDispatcherW",CharSet=CharSet.Unicode,SetLastError=true)]
    [return:MarshalAs(UnmanagedType.Bool)]
    static extern bool StartServiceCtrlDispatcher([In] ServiceTableEntry[] table);
    [DllImport("advapi32.dll",EntryPoint="RegisterServiceCtrlHandlerExW",CharSet=CharSet.Unicode,SetLastError=true)]
    static extern IntPtr RegisterServiceCtrlHandlerEx(string name,ControlCallback callback,IntPtr context);
    [DllImport("advapi32.dll",EntryPoint="SetServiceStatus",SetLastError=true)]
    [return:MarshalAs(UnmanagedType.Bool)]
    static extern bool SetServiceStatus(IntPtr handle,ref ServiceStatus status);

    static int Main(string[] args)
    {
        try
        {
            Guard.Require(OperatingSystem.IsWindows()&&Environment.Is64BitProcess,"WINDOWS_X64_REQUIRED");
            if(args is ["--self-test"]) return SelfTest();
            if(args is ["--verify-staging"])
            {
                PrivateStore.AssertAdmin();
                VerifyStaging();
                Console.WriteLine("HOST_STAGING=PASS; SQL_CONNECTION=NONE; TUNNEL=NONE");
                return 0;
            }
            if(args is ["--verify-layout"])
            {
                PrivateStore.AssertAdmin();
                VerifyPrivateFiles(requireRuntime:true);
                Console.WriteLine("HOST_LAYOUT=PASS; SQL_CONNECTION=NONE; TUNNEL=NONE");
                return 0;
            }
            if(args is ["--provision-runtime",var tunnelId]) {ProvisionRuntime(tunnelId,false);return 0;}
            if(args is ["--rotate-runtime-key"]) {ProvisionRuntime(null,true);return 0;}
            Guard.Require(args.Length==0,"SERVICE_ARGUMENTS_FORBIDDEN");
            var table=new[]{
                new ServiceTableEntry{Name=ServiceName,Callback=MainCallbackRef},
                new ServiceTableEntry{Name=null,Callback=null}
            };
            Guard.Require(StartServiceCtrlDispatcher(table),"SCM_REQUIRED");
            return exitCode;
        }
        catch(Blocked e) {Console.Error.WriteLine("IDENT_HOST_STOP="+e.Message);}
        catch(Exception) {Console.Error.WriteLine("IDENT_HOST_STOP=LOCAL_RUNTIME");}
        return 1;
    }

    static void ServiceMain(int argc,IntPtr argv)
    {
        serviceHandle=RegisterServiceCtrlHandlerEx(ServiceName,ControlCallbackRef,IntPtr.Zero);
        if(serviceHandle==IntPtr.Zero){exitCode=1;return;}
        SetStatus(StartPending,30000);
        try
        {
            PrivateStore.AssertService();
            VerifyPrivateFiles(requireRuntime:true);
            string tunnelId=ReadRuntimeId();
            string key=ReadRuntimeKey();
            try
            {
                using var tunnel=StartTunnel(tunnelId,key);
                // Process started != remotely ready. Require separate /readyz + ChatGPT acceptance.
                SetStatus(Running,0);
                while(!StopRequested.IsSet&&!tunnel.HasExited) StopRequested.Wait(TimeSpan.FromSeconds(1));
                if(!StopRequested.IsSet) throw new Blocked("TUNNEL_EXITED_UNEXPECTEDLY");
                SetStatus(StopPending,15000);
                StopChild(tunnel);
            }
            finally{key=string.Empty;}
            exitCode=0;SetStatus(Stopped,0);
        }
        catch(Exception)
        {
            exitCode=1;SetStatus(Stopped,0,true);
        }
    }

    static int OnControl(int code,int eventType,IntPtr eventData,IntPtr context)
    {
        if(code is ControlStop or ControlShutdown)
        {
            StopRequested.Set();SetStatus(StopPending,15000);
        }
        return 0;
    }
    static void SetStatus(int state,int waitHint,bool failed=false)
    {
        if(serviceHandle==IntPtr.Zero)return;
        var s=new ServiceStatus{
            ServiceType=OwnProcess,CurrentState=state,
            ControlsAccepted=state==Running?AcceptStop|AcceptShutdown:0,
            Win32ExitCode=failed?ErrorServiceSpecific:0,
            ServiceSpecificExitCode=failed?1:0,
            Checkpoint=state is StartPending or StopPending?Interlocked.Increment(ref checkpoint):0,
            WaitHint=waitHint
        };
        SetServiceStatus(serviceHandle,ref s);
    }

    static void VerifyStaging()
    {
        PrivateStore.VerifyDirectoryLayout();
        foreach(string path in new[]{
            Path.Combine(PrivateStore.App,"bridge"),
            Path.Combine(PrivateStore.App,"host"),
            Path.Combine(PrivateStore.App,"tunnel")
        }) PrivateStore.AssertAncestors(path);
        foreach(string path in new[]{BridgeExe,HostExe,TunnelExe})
            PrivateStore.AssertAcl(new FileInfo(path),false);
    }
    static void VerifyPrivateFiles(bool requireRuntime)
    {
        VerifyStaging();
        PrivateStore.VerifyLayout();
        if(requireRuntime)
            foreach(string path in new[]{RuntimeFile,RuntimeSecret})
                PrivateStore.AssertAcl(new FileInfo(path),false);
    }
    static bool ValidId(string text)=>Regex.IsMatch(text,
        @"\Atunnel_(?:[a-z0-9]{4}_)?[a-z0-9]{32}\z",RegexOptions.CultureInvariant);
    static bool ValidKey(string text)=>text.Length is >=3 and <=512 &&
        text.StartsWith("sk-",StringComparison.Ordinal) &&
        Regex.IsMatch(text,@"\A[A-Za-z0-9._~+/-]+=*\z",RegexOptions.CultureInvariant);

    static string ReadRuntimeId()
    {
        using var doc=Guard.Parse(PrivateStore.Read(RuntimeFile,2048,false),2048);
        var obj=Guard.Object(doc.RootElement,"tunnel_id","key_scope");
        string id=Guard.Text(obj,"tunnel_id");
        Guard.Require(ValidId(id)&&Guard.Text(obj,"key_scope")=="AZ-IDENT-RESEARCH/v1",
            "RUNTIME_CONFIG_INVALID");
        return id;
    }
    static string ReadRuntimeKey()
    {
        byte[] settings=PrivateStore.Read(PrivateStore.ConfigPath,8192,false);
        var cfg=ResearchSettings.Read(settings);
        byte[] cipher=PrivateStore.Read(RuntimeSecret,Dpapi.PrivateStoreLimit,false);
        byte[] plaintext=Dpapi.Transform(cipher,cfg.Binding,"tunnel-runtime-key",false);
        try{
            Guard.Require(plaintext.Length is >=6 and <=1024 && plaintext.Length%2==0,"RUNTIME_KEY_FORMAT");
            string key=Encoding.Unicode.GetString(plaintext);
            Guard.Require(ValidKey(key),"RUNTIME_KEY_INVALID");
            return key;
        }finally{CryptographicOperations.ZeroMemory(plaintext);}
    }

    static Process StartTunnel(string tunnelId,string key)
    {
        Guard.Require(ValidId(tunnelId)&&ValidKey(key),"TUNNEL_ARGUMENTS");
        var p=new ProcessStartInfo(TunnelExe){UseShellExecute=false,CreateNoWindow=true};
        foreach(string arg in new[]{
            "run","--control-plane.tunnel-id",tunnelId,
            "--mcp.command","\""+BridgeExe+"\" --stdio",
            "--health.listen-addr","127.0.0.1:0","--log.level=warn"
        })p.ArgumentList.Add(arg);
        p.Environment.Remove("OPENAI_API_KEY");
        p.Environment.Remove("OPENAI_ADMIN_KEY");
        p.Environment["CONTROL_PLANE_API_KEY"]=key;
        p.WorkingDirectory=Path.Combine(PrivateStore.App,"tunnel");
        return Process.Start(p)??throw new Blocked("TUNNEL_START_FAILED");
    }
    static void StopChild(Process p)
    {
        if(p.HasExited)return;
        try{p.Kill(entireProcessTree:true);}
        catch(InvalidOperationException){return;}
        Guard.Require(p.WaitForExit(10000),"TUNNEL_STOP_FAILED");
    }

    static void ProvisionRuntime(string? tunnelId,bool rotate)
    {
        PrivateStore.AssertAdmin();
        VerifyPrivateFiles(requireRuntime:false);
        Guard.Require(rotate || (tunnelId!=null&&ValidId(tunnelId)),"TUNNEL_ID_REQUIRED");
        Guard.Require(!Console.IsInputRedirected,"INTERACTIVE_CONSOLE_REQUIRED");
        Guard.Require(rotate || (!File.Exists(RuntimeFile)&&!File.Exists(RuntimeSecret)),"ALREADY_PROVISIONED");
        Guard.Require(!rotate || (File.Exists(RuntimeFile)&&File.Exists(RuntimeSecret)),"NOT_PROVISIONED");
        if(rotate)_=ReadRuntimeId();
        Console.Error.Write("OpenAI runtime key (hidden): ");
        using var key=new SecureString();
        while(true){
            var k=Console.ReadKey(true);
            if(k.Key==ConsoleKey.Enter)break;
            if(k.Key==ConsoleKey.Escape)throw new Blocked("KEY_CANCELLED");
            if(k.Key==ConsoleKey.Backspace){if(key.Length>0)key.RemoveAt(key.Length-1);continue;}
            if(char.IsControl(k.KeyChar))continue;
            Guard.Require(key.Length<512,"KEY_TOO_LONG");key.AppendChar(k.KeyChar);
        }
        Console.Error.WriteLine();
        key.MakeReadOnly();
        byte[] raw=Dpapi.PasswordBytes(key);
        byte[] cipher=Array.Empty<byte>();
        try{
            Guard.Require(ValidKey(Encoding.Unicode.GetString(raw)),"RUNTIME_KEY_INVALID");
            var cfg=ResearchSettings.Read(PrivateStore.Read(PrivateStore.ConfigPath,8192,false));
            cipher=Dpapi.Transform(raw,cfg.Binding,"tunnel-runtime-key",true);
            if(!rotate){
                var meta=JsonSerializer.SerializeToUtf8Bytes(new{
                    tunnel_id=tunnelId,key_scope="AZ-IDENT-RESEARCH/v1"
                });
                PrivateStore.Publish(RuntimeSecret,cipher,adminOwner:true);
                PrivateStore.Publish(RuntimeFile,meta,adminOwner:true);
            }else{
                PrivateStore.Publish(RuntimeSecret,cipher,replace:true,adminOwner:true);
            }
            PrivateStore.AssertAcl(new FileInfo(RuntimeSecret),false);
            PrivateStore.AssertAcl(new FileInfo(RuntimeFile),false);
            Console.WriteLine("RUNTIME_PROVISION=PASS; KEY=HIDDEN; TUNNEL_NOT_STARTED; SQL_CONNECTION=NONE");
        }finally{
            CryptographicOperations.ZeroMemory(raw);
            CryptographicOperations.ZeroMemory(cipher);
        }
    }
    static int SelfTest()
    {
        Guard.Require(ValidId("tunnel_0123456789abcdef0123456789abcdef"),"TEST_ID");
        Guard.Require(!ValidId("tunnel_bad"),"TEST_ID");
        Guard.Require(ValidKey("sk-TEST_TEST"),"TEST_KEY");
        Guard.Require(!ValidKey("sk-TEST\nBAD"),"TEST_KEY");
        Guard.Require(!ValidKey("other-token"),"TEST_KEY");
        Guard.Require(!ValidKey("sk-TEST "),"TEST_KEY");
        Console.WriteLine("IDENT_HOST_SELF_TEST=6/6 PASS; SERVICE=NONE; TUNNEL=NONE; SQL_CONNECTION=NONE");
        return 0;
    }
}
