// TEST ONLY. Built separately against the exact audited sources, never shipped.
using System.Security;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.Data.SqlClient;

internal static class SqlLab
{
    static void Check(bool ok,string code){if(!ok)throw new Exception(code);}
    static string Read(string p)=>File.ReadAllText(p,Encoding.UTF8).Trim();
    static SecureString Password(string lab)
    {
        byte[] raw=File.ReadAllBytes(Path.Combine(lab,"sql-password.synthetic"));
        var pw=new SecureString();
        try {
            Check(raw.Length is >0 and <=128,"FIXTURE_PASSWORD");
            foreach(byte ch in raw)pw.AppendChar((char)ch);
            pw.MakeReadOnly();return pw;
        }catch{pw.Dispose();throw;}
        finally{CryptographicOperations.ZeroMemory(raw);}
    }
    static DateOnly First(string lab)=>Guard.Date(Read(Path.Combine(lab,"first-day.txt")));
    static SqlReader.Config SqlConfig(string lab,string mode)
    {
        string cert=Path.Combine(lab,mode=="wrong-pin"?"wrong":"good","server.cer");
        return new("127.0.0.1",15433,"IdentTlsLab","ident_lab_reader",cert,
            mode=="wrong-hash"?new string('0',64):Guard.Hash(File.ReadAllBytes(cert)),
            Guard.Day(First(lab)),Guard.Day(First(lab).AddDays(4)),Guard.Query);
    }
    static void VerifyRows(SqlReader.Result result,DateOnly first)
    {
        Check(result.Precision==18&&result.Scale==2&&result.Rows.Count==3,"SQL_SCHEMA_OR_ROWS");
        var r=result.Rows;
        Check(r[0].date==Guard.Day(first)&&r[0].records_count==2&&r[0].paying_patients==1&&r[0].positive_amount=="120.05"&&r[0].negative_amount=="-10.05"&&r[0].net_amount=="110.00","SQL_DAY_0");
        Check(r[1].date==Guard.Day(first.AddDays(1))&&r[1].records_count==1&&r[1].paying_patients==1&&r[1].net_amount=="25.00","SQL_DAY_1");
        Check(r[2].date==Guard.Day(first.AddDays(3))&&r[2].records_count==2&&r[2].paying_patients==1&&r[2].positive_amount=="13.00"&&r[2].negative_amount=="0.00"&&r[2].net_amount=="13.00","SQL_DAY_3");
    }
    static void Probe(string lab,string mode)
    {
        using var pw=Password(lab);
        SqlReader.Result? result=null;
        try{
            result=SqlReader.Collect(SqlConfig(lab,mode),pw,CancellationToken.None);
        }catch(Blocked e){
            Check((mode=="wrong-hash"&&e.Message=="PIN_SHA256_MISMATCH")||
                  (mode=="extra-right"&&e.Message=="SQL_READONLY_GUARD_FAILED"),"UNEXPECTED_GUARD_FAILURE");
            Console.WriteLine("SQL_PROBE="+mode+"; PASS; GUARD="+e.Message);return;
        }catch(SqlException e){
            Check(mode=="wrong-pin"&&e.Number!=18456&&e.Number!=18452,"UNEXPECTED_SQL_FAILURE_"+e.Number);
            Console.WriteLine("SQL_PROBE=wrong-pin; PASS; TLS_CONNECTION_REJECTED; SQL_ERROR_NUMBER="+e.Number);return;
        }
        Check(mode=="positive"&&result!=null,"UNEXPECTED_SQL_SUCCESS");
        VerifyRows(result!,First(lab));
        Console.WriteLine("SQL_PROBE=positive; PASS; EXACT_AGGREGATES=YES");
    }
    static void ProvisionFixture(string sqlLab,string httpLab)
    {
        byte[] pin=File.ReadAllBytes(Path.Combine(sqlLab,"good","server.cer"));
        byte[] token=Encoding.ASCII.GetBytes(Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant());
        var now=DateTimeOffset.UtcNow;
        var first=First(sqlLab);
        var date=Guard.Day(first);
        byte[] seed=JsonSerializer.SerializeToUtf8Bytes(new {
            contract_version=1,dataset=Guard.Dataset,scope="whole_source_database",
            source_id=Guid.NewGuid().ToString("D"),stream_id=Guid.NewGuid().ToString("D"),
            batch_id=Guid.NewGuid().ToString("D"),sequence=1,source_timezone="Asia/Novosibirsk",
            date_from=date,date_to_exclusive=Guard.Day(first.AddDays(1)),
            query_started_at=Guard.UtcText(now.AddSeconds(-1)),observed_at=Guard.UtcText(now),
            coverage="full_query_window_as_observed",query_sha256=Guard.Query,
            schema=new {payment_precision=18,payment_scale=2},
            rows=new[]{new SqlReader.Row(date,2,1,"120.05","-10.05","110.00")}
        });
        byte[] template=JsonSerializer.SerializeToUtf8Bytes(new {
            organization_id=Guid.NewGuid().ToString("D"),source_timezone="Asia/Novosibirsk",
            sql_host="127.0.0.1",sql_port=15433,sql_database="IdentTlsLab",sql_user="ident_lab_reader",
            certificate_sha256=Guard.Hash(pin),https_origin="https://ident-lab.test",
            interval_minutes=15,lookback_days=31,max_history_bytes=32*1024*1024,min_free_bytes=16*1024*1024
        });
        try{
            using var pw=Password(sqlLab);
            var config=Provision.MakeConfig(template,seed,token);
            var cfg=Provision.Write(config,seed,pin,pw,token);
            Check(Provision.CheckLocal().Source==cfg.Source,"PROVISION_CHECK");
            File.WriteAllBytes(Path.Combine(httpLab,"seed.json"),seed);
            File.WriteAllBytes(Path.Combine(httpLab,"token.txt"),token);
            File.WriteAllBytes(Path.Combine(httpLab,"config.json"),config);
            File.WriteAllText(Path.Combine(httpLab,"first-day.txt"),date,new UTF8Encoding(false));
            Console.WriteLine("SQL_SERVICE_FIXTURE_PROVISIONED=PASS; PIN=REAL_LOCAL_SQL_CERT; SEED=ONE_SYNTHETIC_DAY");
        }finally{CryptographicOperations.ZeroMemory(token);}
    }
    static void CheckJournal(string httpLab)
    {
        var cfg=Provision.CheckLocal();
        var j=new Journal(cfg,PrivateStore.JournalDir,DateTimeOffset.UtcNow);
        Check(j.LastSequence==2&&j.Acked==2,"JOURNAL_ACK_SEQUENCE");
        string name="packet-0000000000000002.bin";
        var path=Path.Combine(PrivateStore.JournalDir,name);
        byte[] cipher=PrivateStore.Read(path,Dpapi.PrivateStoreLimit,true);
        byte[] raw=Dpapi.Transform(cipher,cfg.Binding,name,false);
        try{
            Check(!cipher.AsSpan().SequenceEqual(raw),"PLAINTEXT_PACKET");
            var received=File.ReadAllBytes(Path.Combine(httpLab,"accepted-2.json"));
            Check(raw.AsSpan().SequenceEqual(received),"RECEIVER_BYTES_MISMATCH");
            Check(Batch.Read(raw,cfg,DateTimeOffset.UtcNow).Sequence==2,"BATCH_VALIDATE");
            Console.WriteLine("SQL_SERVICE_JOURNAL=PASS; LOCAL_SEQUENCE=2; ACKED=2; EXACT_RECEIVER_BYTES=YES; ENCRYPTED=YES");
        }finally{CryptographicOperations.ZeroMemory(raw);}
    }
    static int Main(string[] args)
    {
        try{
            Check(Environment.GetEnvironmentVariable("GITHUB_ACTIONS")=="true"&&
                Environment.GetEnvironmentVariable("RUNNER_ENVIRONMENT")=="github-hosted"&&
                Environment.GetEnvironmentVariable("RUNNER_OS")=="Windows"&&
                Environment.GetEnvironmentVariable("GITHUB_REF")=="refs/heads/test/ident-006c-sender-20261126","DISPOSABLE_RUNNER");
            string sqlLab=Path.GetFullPath(Environment.GetEnvironmentVariable("IDENT_LAB_DIR")!);
            string httpLab=Path.Combine(Environment.GetEnvironmentVariable("RUNNER_TEMP")!,"ident006c-lab");
            Check(args.Length==1,"EXPLICIT_TEST_MODE");
            if(args[0]=="provision")ProvisionFixture(sqlLab,httpLab);
            else if(args[0]=="journal")CheckJournal(httpLab);
            else {
                Check(new[]{"positive","wrong-pin","wrong-hash","extra-right"}.Contains(args[0]),"TEST_MODE");
                Probe(sqlLab,args[0]);
            }
            return 0;
        }catch(Exception e){Console.Error.WriteLine("SQL_LAB_FAILURE="+e.GetType().Name+"; "+(e is SqlException sql?"SQL_NUMBER_"+sql.Number:e is Blocked?e.Message:e.Message));return 1;}
    }
}
