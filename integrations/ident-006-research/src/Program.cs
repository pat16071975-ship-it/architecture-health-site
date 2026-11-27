using System.ComponentModel;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using Microsoft.Data.SqlClient;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using ModelContextProtocol;
using ModelContextProtocol.Server;

internal static class Program
{
    private static void Provision(string configPath,string certPath)
    {
        PrivateStore.AssertAdmin();
        PrivateStore.VerifyDirectoryLayout();
        PrivateStore.AssertNoOrdinaryShares();
        foreach(var p in new[]{PrivateStore.ConfigPath,PrivateStore.Pin,PrivateStore.SqlSecret})
            Guard.Require(!File.Exists(p)&&!Directory.Exists(p),"ALREADY_PROVISIONED_OR_PARTIAL");
        var raw=PrivateStore.ReadInput(configPath,8192);
        var cfg=ResearchSettings.Read(raw);
        var pin=PrivateStore.ReadInput(certPath,65536);
        Guard.Require(Guard.Hash(pin)==cfg.PinHash,"PIN_SHA256_MISMATCH");
        using var cert=X509CertificateLoader.LoadCertificate(pin);
        Guard.Require(cert.RawData.AsSpan().SequenceEqual(pin)&&!cert.HasPrivateKey&&DateTime.UtcNow>=cert.NotBefore.ToUniversalTime()&&DateTime.UtcNow<=cert.NotAfter.ToUniversalTime(),"PIN_DER_OR_VALIDITY");
        Console.Error.Write("SQL password: ");
        using var password=Dpapi.ConsoleSecret();
        var pw=Dpapi.PasswordBytes(password);
        try{
            var sealedPw=Dpapi.Transform(pw,cfg.Binding,"sql-password",true);
            try{
                PrivateStore.Publish(PrivateStore.Pin,pin,adminOwner:true);
                PrivateStore.Publish(PrivateStore.SqlSecret,sealedPw,adminOwner:true);
                PrivateStore.Publish(PrivateStore.ConfigPath,raw,adminOwner:true);
            }finally{CryptographicOperations.ZeroMemory(sealedPw);}
        }finally{CryptographicOperations.ZeroMemory(pw);}
        Console.WriteLine("PROVISION=PASS; SQL_CONNECTION=NONE; TUNNEL=NONE");
    }
    private static async Task<int> Main(string[] args)
    {
        try{
            Guard.Require(OperatingSystem.IsWindows()&&Environment.Is64BitProcess,"WINDOWS_X64_REQUIRED");
            if(args.Length==1&&args[0]=="--self-test"){SelfTest.Run();return 0;}
            if(args.Length==3&&args[0]=="--provision"){Provision(args[1],args[2]);return 0;}
            Guard.Require(args.Length==1&&args[0]=="--stdio","MODE_REQUIRED");
            PrivateStore.AssertService();PrivateStore.VerifyLayout();
            var builder=Host.CreateApplicationBuilder(new HostApplicationBuilderSettings{Args=Array.Empty<string>(),DisableDefaults=true});
            builder.Logging.ClearProviders();
            builder.Services.AddSingleton<ResearchEngine>();
            builder.Services.AddMcpServer().WithStdioServerTransport().WithTools<ResearchTools>();
            using var host=builder.Build();
            await host.RunAsync();
            return 0;
        }catch(Blocked e){Console.Error.WriteLine("IDENT_RESEARCH_STOP="+e.Message);}
        catch(Exception){Console.Error.WriteLine("IDENT_RESEARCH_STOP=LOCAL_RUNTIME");}
        return 1;
    }
}
public sealed class ResearchEngine
{
    private readonly SemaphoreSlim gate=new(1,1);
    internal string Execute(string name,string from,string to,int? staff,CancellationToken ct)
    {
        bool held=false;
        try{
            Guard.Require(name is "ident.hours.sql" or "ident.primary.sql","UNKNOWN_REPORT");
            var window=ReportWindow.Parse(from,to,staff);
            Guard.Require(name=="ident.hours.sql"?staff is null:staff is >0,"STAFF_ID_REQUIRED");
            held=gate.Wait(0,ct);
            Guard.Require(held,"REPORT_BUSY");
            PrivateStore.AssertService();PrivateStore.VerifyLayout();
            using var processLock=PrivateStore.OpenLock(Path.Combine(PrivateStore.State,"research.lock"));
            var cfg=ResearchSettings.Read(PrivateStore.Read(PrivateStore.ConfigPath,8192,false));
            using var password=Dpapi.ReadPassword(cfg);
            using var deadline=CancellationTokenSource.CreateLinkedTokenSource(ct);
            deadline.CancelAfter(TimeSpan.FromSeconds(45));
            var result=ResearchSql.Collect(cfg,name,window,password,deadline.Token);
            var raw=JsonSerializer.SerializeToUtf8Bytes(result);
            Guard.Require(raw.Length<=192*1024,"REPORT_RESULT_LIMIT");
            return System.Text.Encoding.UTF8.GetString(raw);
        }catch(Blocked e){throw new McpException(e.Message);}
        catch(SqlException){throw new McpException("SQL_READ_FAILED");}
        catch(OperationCanceledException){throw new McpException("REPORT_CANCELLED_OR_TIMED_OUT");}
        catch(IOException){throw new McpException("REPORT_BUSY_OR_LOCAL_STORE_FAILED");}
        catch(Exception){throw new McpException("REPORT_UNAVAILABLE");}
        finally{if(held)gate.Release();}
    }
}
public sealed class ResearchTools(ResearchEngine engine)
{
    [McpServerTool(Name="ident_status",ReadOnly=true,Destructive=false,Idempotent=true,OpenWorld=false)]
    [Description("Read-only IDENT research service capability status. Does not query SQL; not evidence of database availability.")]
    public string Status()=>"{\"service\":\"IDENT research\",\"mode\":\"read_only\",\"sql_connection\":\"not_checked\",\"catalog\":\"v1\",\"reports\":[\"specialist_performance\",\"primary_repeat\"]}";
    [McpServerTool(Name="ident_specialist_performance",ReadOnly=true,Destructive=false,Idempotent=true,OpenWorld=false)]
    [Description("Read aggregate staff schedule, completed actual reception durations, remaining booked durations, reserves, completed and future visit counts, and discounted rendered-service revenue directly from IDENT. Current database state, not a historical schedule snapshot. Date window 1-31 days. Names and staff IDs are staff only, never patient IDs. Use linked-completed revenue divided by actual seconds for revenue per actual hour; total revenue may include unlinked services. Future revenue is an estimate using that rate. No departmental mapping is inferred.")]
    public string Performance([Description("Inclusive local date YYYY-MM-DD")] string date_from,
        [Description("Exclusive local date YYYY-MM-DD, 1-31 days after start")] string date_to_exclusive,
        CancellationToken cancellationToken)=>engine.Execute("ident.hours.sql",date_from,date_to_exclusive,null,cancellationToken);
    [McpServerTool(Name="ident_primary_repeat",ReadOnly=true,Destructive=false,Idempotent=true,OpenWorld=false)]
    [Description("Read completed visit counts, distinct patient counts and linked discounted service revenue for one specialist, by first completed visit with that specialist and by first completed visit in the clinic. First observed in available database history, not the native IDENT report flag. No patient IDs or patient rows leave SQL. Distinct patient counts for primary and repeat categories can overlap. Use specialist performance to find the staff ID.")]
    public string PrimaryRepeat([Description("Positive staff ID from specialist performance")] int staff_id,
        [Description("Inclusive local date YYYY-MM-DD")] string date_from,
        [Description("Exclusive local date YYYY-MM-DD, 1-31 days after start")] string date_to_exclusive,
        CancellationToken cancellationToken)=>engine.Execute("ident.primary.sql",date_from,date_to_exclusive,staff_id,cancellationToken);
}
