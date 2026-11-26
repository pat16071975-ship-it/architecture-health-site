using System.Runtime.InteropServices;
using System.Security;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;
using Microsoft.Data.SqlClient;

internal static class Provision
{
    internal static void LayoutBefore()
    {
        PrivateStore.AssertAdmin();
        PrivateStore.AssertAncestors(PrivateStore.Root);
        foreach(string p in new[]{PrivateStore.Root,PrivateStore.App})PrivateStore.AssertAcl(new DirectoryInfo(p),false);
        foreach(string p in new[]{PrivateStore.State,PrivateStore.JournalDir})PrivateStore.AssertAcl(new DirectoryInfo(p),true);
        PrivateStore.AssertNoOrdinaryShares();
        foreach(string p in new[]{PrivateStore.ConfigPath,PrivateStore.Pin,PrivateStore.SqlSecret,PrivateStore.TokenSecret})
            Guard.Require(!File.Exists(p)&&!Directory.Exists(p),"ALREADY_PROVISIONED_OR_PARTIAL");
        Guard.Require(!Directory.EnumerateFileSystemEntries(PrivateStore.JournalDir).Any(),"INITIAL_JOURNAL_NOT_EMPTY");
    }

    internal static byte[] MakeConfig(byte[] template,byte[] seed,byte[] token)
    {
        using var configDoc=Guard.Parse(template,8192);
        using var seedDoc=Guard.Parse(seed);
        var d=Guard.Object(configDoc.RootElement,"organization_id","source_timezone","sql_host","sql_port","sql_database","sql_user",
            "certificate_sha256","https_origin","interval_minutes","lookback_days","max_history_bytes","min_free_bytes");
        // The first real batch is the sole source of source/stream identifiers.
        // Full contract validation below also rejects duplicate fields.
        var root=seedDoc.RootElement;
        d.Add("source_id",root.GetProperty("source_id").Clone());
        d.Add("stream_id",root.GetProperty("stream_id").Clone());
        d.Add("token_sha256",JsonSerializer.SerializeToElement(Guard.Hash(token)));
        d.Add("initial_batch_sha256",JsonSerializer.SerializeToElement(Guard.Hash(seed)));
        return JsonSerializer.SerializeToUtf8Bytes(d);
    }

    internal static void VerifyPin(byte[] raw, Settings cfg)
    {
        Guard.Require(raw.Length<=65536&&Guard.Hash(raw)==cfg.PinHash,"PIN_SHA256_MISMATCH");
        using var cert=X509CertificateLoader.LoadCertificate(raw);
        var now=DateTime.UtcNow;
        Guard.Require(cert.RawData.AsSpan().SequenceEqual(raw)&&!cert.HasPrivateKey&&now>=cert.NotBefore.ToUniversalTime()&&now<=cert.NotAfter.ToUniversalTime(),"PIN_DER_OR_VALIDITY");
    }

    internal static Settings Write(byte[] config,byte[] seed,byte[] pin,SecureString password,byte[] token)
    {
        // Shared by the interactive provisioner and the separately compiled
        // synthetic test assembly. No production CLI accepts a plaintext secret.
        LayoutBefore();
        var cfg=Settings.Read(config);
        var batch=Batch.Read(seed,cfg,DateTimeOffset.UtcNow);
        Guard.Require(batch.Sequence==1&&batch.Digest==cfg.SeedDigest,"INITIAL_SEQUENCE");
        VerifyPin(pin,cfg);
        Guard.Require(token.Length==64&&Guard.Hash(token)==cfg.TokenHash,"TOKEN_BINDING");
        byte[] pw=Dpapi.PasswordBytes(password);
        try
        {
            byte[] sealedPw=Dpapi.Transform(pw,cfg.Binding,"sql-password",true);
            byte[] sealedToken=Dpapi.Transform(token,cfg.Binding,"http-token",true);
            try
            {
                PrivateStore.Publish(PrivateStore.Pin,pin,adminOwner:true);
                PrivateStore.Publish(PrivateStore.SqlSecret,sealedPw,adminOwner:true);
                PrivateStore.Publish(PrivateStore.TokenSecret,sealedToken,adminOwner:true);
                Journal.Seed(cfg,PrivateStore.JournalDir,seed,DateTimeOffset.UtcNow);
                // Config is the last publication marker. A partial provisioning
                // never activates the service; retain files for recovery.
                PrivateStore.Publish(PrivateStore.ConfigPath,config,adminOwner:true);
                return cfg;
            }
            finally{CryptographicOperations.ZeroMemory(sealedPw);CryptographicOperations.ZeroMemory(sealedToken);}
        }
        finally{CryptographicOperations.ZeroMemory(pw);}
    }

    internal static void Registration(Settings cfg)
    {
        // No SQL password, bearer token, packet rows or actual sums are output.
        Console.WriteLine(JsonSerializer.Serialize(new {
            organization_id=cfg.Organization,source_id=cfg.Source,stream_id=cfg.Stream,source_timezone=cfg.ZoneName,token_sha256=cfg.TokenHash
        }));
        Console.WriteLine("REGISTRATION=PASS; TOKEN_PLAINTEXT=NONE; FINANCIAL_TRANSFER=NONE");
    }

    internal static void Interactive(string templatePath,string seedPath,string certPath)
    {
        LayoutBefore();
        byte[] template=PrivateStore.ReadInput(templatePath,8192),seed=PrivateStore.ReadInput(seedPath,Guard.MaxPayload),pin=PrivateStore.ReadInput(certPath,65536);
        byte[] random=RandomNumberGenerator.GetBytes(32);
        byte[] token=Encoding.ASCII.GetBytes(Convert.ToHexString(random).ToLowerInvariant());
        CryptographicOperations.ZeroMemory(random);
        try
        {
            byte[] config=MakeConfig(template,seed,token);
            var cfg=Settings.Read(config);
            var b=Batch.Read(seed,cfg,DateTimeOffset.UtcNow);
            Guard.Require(b.Sequence==1,"INITIAL_SEQUENCE");
            VerifyPin(pin,cfg);
            Console.Error.Write("SQL password: ");
            using var password=Dpapi.ConsoleSecret();
            cfg=Write(config,seed,pin,password,token);
            Registration(cfg);
        }
        finally{CryptographicOperations.ZeroMemory(seed);CryptographicOperations.ZeroMemory(token);}
    }

    internal static Settings CheckLocal()
    {
        PrivateStore.AssertAdmin();
        PrivateStore.VerifyLayout();
        var cfg=Settings.Read(PrivateStore.Read(PrivateStore.ConfigPath,8192,false));
        VerifyPin(PrivateStore.Read(PrivateStore.Pin,65536,false),cfg);
        using var password=Dpapi.ReadPassword(cfg);
        byte[] token=Dpapi.ReadToken(cfg);
        CryptographicOperations.ZeroMemory(token);
        _=new Journal(cfg,PrivateStore.JournalDir,DateTimeOffset.UtcNow);
        return cfg;
    }
}

internal static class Runner
{
    private static void Status(string code,Journal? journal=null)
    {
        PrivateStore.AssertAcl(new DirectoryInfo(PrivateStore.State),true);
        string target=Path.Combine(PrivateStore.State,"status.json");
        if(File.Exists(target)) _=PrivateStore.Read(target,8192,true);
        byte[] raw=JsonSerializer.SerializeToUtf8Bytes(new {
            status=code,time_utc=Guard.UtcText(DateTimeOffset.UtcNow),
            local_sequence=journal?.LastSequence,acked_sequence=journal?.Acked
        });
        PrivateStore.Publish(target,raw,replace:true);
    }
    private static async Task Delay(int seconds,CancellationToken ct)=>await Task.Delay(TimeSpan.FromSeconds(seconds),ct).ConfigureAwait(false);

    internal static async Task<int> Run(CancellationToken ct)
    {
        Journal? journal=null;
        FileStream? runLock=null;
        bool stateVerified=false;
        try
        {
            PrivateStore.AssertService();
            PrivateStore.VerifyLayout();
            // FileShare.None across all sessions/processes for this one source.
            string lockPath=Path.Combine(PrivateStore.State,"service.lock");
            if(File.Exists(lockPath))PrivateStore.AssertAcl(new FileInfo(lockPath),true);
            runLock=PrivateStore.OpenLock(lockPath);
            stateVerified=true;
            var cfg=Settings.Read(PrivateStore.Read(PrivateStore.ConfigPath,8192,false));
            Provision.VerifyPin(PrivateStore.Read(PrivateStore.Pin,65536,false),cfg);
            journal=new Journal(cfg,PrivateStore.JournalDir,DateTimeOffset.UtcNow);
            using var receiver=new Receiver(cfg);
            byte[] Collect(DateOnly start,DateOnly end,CancellationToken cancel)
            {
                // The immutable query is embedded in the executable, not loaded
                // from an editable SQL file or received over the network.
                using var pw=Dpapi.ReadPassword(cfg);
                var sql=new SqlReader.Config(cfg.Host,cfg.Port,cfg.Database,cfg.User,PrivateStore.Pin,cfg.PinHash,
                    Guard.Day(start),Guard.Day(end),Guard.Query);
                try
                {
                    var result=SqlReader.Collect(sql,pw,cancel);
                    return Batch.Create(cfg,journal.LastSequence+1,start,end,result);
                }
                catch(SqlException e) when(e.Number==18456 || e.Number==18452)
                {throw new Blocked("SQL_AUTHENTICATION");}
                catch(SqlException){throw new Retry("SQL_CONNECTION_OR_QUERY");}
            }
            var cycle=new Cycle(cfg,journal,receiver,Collect,()=>DateTimeOffset.UtcNow);
            int backoff=30;
            Status("RUNNING",journal);
            while(!ct.IsCancellationRequested)
            {
                try
                {
                    // Recheck ACLs and ordinary shares at every cycle, including
                    // after an outage. Changes never trigger automatic repair.
                    PrivateStore.VerifyLayout();
                    Guard.Require(CryptographicOperations.FixedTimeEquals(SHA256.HashData(PrivateStore.Read(PrivateStore.ConfigPath,8192,false)),cfg.Binding),"CONFIG_CHANGED");
                    string result=await cycle.Tick(ct).ConfigureAwait(false);
                    Status(result,journal);
                    backoff=30;
                    await Delay(result=="IDLE"?30:1,ct).ConfigureAwait(false);
                }
                catch(Retry e)
                {
                    Status(e.Message,journal);
                    await Delay(backoff,ct).ConfigureAwait(false);
                    backoff=Math.Min(900,backoff*2);
                }
                catch(Blocked e)
                {
                    Status("BLOCKED_"+e.Message,journal);
                    // A conflict, bad receipt, failed permissions or permanent
                    // rejection is not automatically retried. Operator review
                    // and explicit restart are required.
                    await Task.Delay(Timeout.Infinite,ct).ConfigureAwait(false);
                }
            }
            return 0;
        }
        catch(OperationCanceledException) when(ct.IsCancellationRequested){return 0;}
        catch(Blocked e)
        {
            // A startup integrity error leaves the service running but blocked
            // when possible, not an infinite credential/SQL login retry loop.
            if(!stateVerified)return 1;
            try{Status("BLOCKED_"+e.Message,journal);await Task.Delay(Timeout.Infinite,ct).ConfigureAwait(false);}
            catch(OperationCanceledException) when(ct.IsCancellationRequested){return 0;}
            catch(Exception){}
            return 1;
        }
        catch(Exception)
        {
            if(stateVerified)try{Status("LOCAL_RUNTIME_FAILURE",journal);}catch(Exception){}
            return 1;
        }
        finally{runLock?.Dispose();}
    }
}

internal static class Scm
{
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] private delegate void ServiceMain(uint count,IntPtr argv);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] private delegate uint Handler(uint control,uint type,IntPtr data,IntPtr context);
    [StructLayout(LayoutKind.Sequential)] private struct Entry {public IntPtr Name,Main;}
    [StructLayout(LayoutKind.Sequential)] private struct ServiceStatus
    { public uint Type,State,Controls,Win32Exit,SpecificExit,Checkpoint,WaitHint; }
    [DllImport("advapi32.dll",CharSet=CharSet.Unicode,ExactSpelling=true,SetLastError=true)]
    [return:MarshalAs(UnmanagedType.Bool)] private static extern bool StartServiceCtrlDispatcherW([In] Entry[] table);
    [DllImport("advapi32.dll",CharSet=CharSet.Unicode,ExactSpelling=true,SetLastError=true)]
    private static extern IntPtr RegisterServiceCtrlHandlerExW(string name,Handler handler,IntPtr context);
    [DllImport("advapi32.dll",SetLastError=true)]
    [return:MarshalAs(UnmanagedType.Bool)] private static extern bool SetServiceStatus(IntPtr handle,ref ServiceStatus status);
    private static readonly ServiceMain MainCallback=Service;
    private static readonly Handler HandlerCallback=Control;
    private static int stopRequested,exitCode=1;
    private static IntPtr handle;
    private static uint Control(uint control,uint type,IntPtr data,IntPtr context)
    {
        if(control is 1 or 5){Interlocked.Exchange(ref stopRequested,1);return 0;}
        if(control==4)return 0;
        return 120;
    }
    private static void Report(uint state,uint check=0,uint hint=0,uint error=0)
    {
        var status=new ServiceStatus{Type=0x10,State=state,Controls=state==4?5u:0,
            Win32Exit=error==0?0u:1066u,SpecificExit=error,Checkpoint=check,WaitHint=hint};
        if(!SetServiceStatus(handle,ref status))Interlocked.Exchange(ref stopRequested,1);
    }
    private static void Service(uint count,IntPtr argv)
    {
        handle=RegisterServiceCtrlHandlerExW(PrivateStore.ServiceName,HandlerCallback,IntPtr.Zero);
        if(handle==IntPtr.Zero)return;
        try
        {
            Report(2,1,30000);
            using var cts=new CancellationTokenSource();
            var task=Task.Run(()=>Runner.Run(cts.Token));
            Report(4);
            uint checkpoint=0;
            Task? cancel=null;
            while(!task.IsCompleted)
            {
                if(Volatile.Read(ref stopRequested)!=0)
                {
                    if(cancel==null)cancel=cts.CancelAsync();
                    Report(3,++checkpoint,30000);
                }
                Thread.Sleep(1000);
            }
            exitCode=task.GetAwaiter().GetResult();
            if(cancel!=null)cancel.GetAwaiter().GetResult();
        }
        catch(Exception){exitCode=1;}
        finally{Report(1,error:(uint)exitCode);}
    }
    internal static int Dispatch()
    {
        IntPtr name=Marshal.StringToHGlobalUni(PrivateStore.ServiceName);
        try
        {
            Entry[] table={new(){Name=name,Main=Marshal.GetFunctionPointerForDelegate(MainCallback)},default};
            if(!StartServiceCtrlDispatcherW(table))return 1;
            return exitCode;
        }
        finally{Marshal.FreeHGlobal(name);}
    }
}

internal static class Program
{
    private static int Main(string[] args)
    {
        try
        {
            Guard.Require(OperatingSystem.IsWindows()&&Environment.Is64BitProcess,"WINDOWS_X64_REQUIRED");
            if(args.Length==1&&args[0]=="--service"){PrivateStore.AssertService();return Scm.Dispatch();}
            if(args.Length==4&&args[0]=="--provision"){Provision.Interactive(args[1],args[2],args[3]);return 0;}
            if(args.Length==1&&args[0]=="--check-local"){Provision.Registration(Provision.CheckLocal());Console.WriteLine("LOCAL_CHECK=PASS; SQL=NONE; HTTP=NONE");return 0;}
            throw new Blocked("EXPLICIT_MODE_REQUIRED");
        }
        catch(Blocked e){Console.Error.WriteLine("STOP="+e.Message);}
        catch(Exception){Console.Error.WriteLine("STOP=LOCAL_VALIDATION_OR_RUNTIME");}
        return 1;
    }
}
