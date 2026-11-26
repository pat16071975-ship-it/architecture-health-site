// Only the separately built synthetic test executable. Not published as the sender.
using System.Security;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

internal static class Tests
{
    private static int count;
    private static void Test(string name,Action action){action();count++;Console.WriteLine("TEST="+name+"; PASS");}
    private static void Check(bool value){if(!value)throw new Exception("TEST_ASSERT");}
    private static void Rejected(Action action)
    {
        try{action();}catch(Blocked){return;}
        throw new Exception("EXPECTED_REJECTION");
    }
    private static void Retried(Action action)
    {
        try{action();}catch(Retry){return;}
        throw new Exception("EXPECTED_RETRY");
    }
    private const string Org="11111111-1111-4111-8111-111111111111";
    private const string Source="22222222-2222-4222-8222-222222222222";
    private const string Stream="33333333-3333-4333-8333-333333333333";
    private static readonly byte[] Token=Encoding.ASCII.GetBytes(new string('a',64));
    private static byte[] Seed(DateTimeOffset now,string amount="123.4567890123")
    {
        string date=Guard.Day(Guard.LocalDay(now,Guard.Zone("Asia/Novosibirsk")));
        string end=Guard.Day(Guard.Date(date).AddDays(1));
        return JsonSerializer.SerializeToUtf8Bytes(new {
            contract_version=1,dataset=Guard.Dataset,scope="whole_source_database",source_id=Source,stream_id=Stream,
            batch_id="44444444-4444-4444-8444-444444444444",sequence=1,source_timezone="Asia/Novosibirsk",
            date_from=date,date_to_exclusive=end,query_started_at=Guard.UtcText(now.AddSeconds(-1)),observed_at=Guard.UtcText(now),
            coverage="full_query_window_as_observed",query_sha256=Guard.Query,schema=new {payment_precision=18,payment_scale=10},
            rows=new[]{new SqlReader.Row(date,1,1,amount,"0",amount)}
        });
    }
    private static byte[] Template(string pinHash)=>JsonSerializer.SerializeToUtf8Bytes(new {
        organization_id=Org,source_timezone="Asia/Novosibirsk",sql_host="127.0.0.1",sql_port=15433,
        sql_database="SyntheticOnly",sql_user="synthetic_readonly",certificate_sha256=pinHash,https_origin="https://ident-lab.test",
        interval_minutes=1440,lookback_days=31,max_history_bytes=32*1024*1024,min_free_bytes=16*1024*1024
    });
    private static byte[] Mutate(byte[] raw,Action<JsonObject> change)
    {var o=JsonNode.Parse(raw)!.AsObject();change(o);return JsonSerializer.SerializeToUtf8Bytes(o);}
    private static byte[] Ack(Batch b,Settings cfg,DateTimeOffset now,string status="accepted")=>JsonSerializer.SerializeToUtf8Bytes(new {
        contract_version=1,dataset=Guard.Dataset,source_id=cfg.Source,stream_id=cfg.Stream,batch_id=b.Id,sequence=b.Sequence,
        payload_sha256=b.Digest,accepted_at=Guard.UtcText(now),status
    });
    private static byte[] Status(Settings cfg,long seq,DateTimeOffset now)=>JsonSerializer.SerializeToUtf8Bytes(new {
        status="ready",dataset=Guard.Dataset,source_id=cfg.Source,stream_id=cfg.Stream,accepted_batches=seq,
        max_sequence=seq==0?(long?)null:seq,last_accepted_at=seq==0?null:Guard.UtcText(now),
        first_observed_day=seq==0?null:"2026-01-01",last_observed_day=seq==0?null:"2026-01-01",mode="shadow_only"
    });

    private static void Unit()
    {
        var now=new DateTimeOffset(2026,1,20,12,0,0,TimeSpan.Zero);
        var seed=Seed(now);
        var config=Provision.MakeConfig(Template(new string('a',64)),seed,Token);
        var cfg=Settings.Read(config);
        var batch=Batch.Read(seed,cfg,now);
        Test("BATCH_CONTRACT_AND_EXACT_DECIMALS",()=>{
            Check(batch.Sequence==1&&batch.Scale==10&&batch.Digest==Guard.Hash(seed));
            Check(Guard.Money("9999999999999999999999999999.1234567890",10)==System.Numerics.BigInteger.Parse("99999999999999999999999999991234567890"));
        });
        Test("SOURCE_AND_SEED_BINDING",()=>{Check(cfg.Source==Source&&cfg.Stream==Stream&&cfg.SeedDigest==Guard.Hash(seed));Rejected(()=>Batch.Read(Mutate(seed,o=>o["source_id"]=Org),cfg,now));});
        Test("DUPLICATE_JSON_FIELDS",()=>{Rejected(()=>Batch.Read(Encoding.UTF8.GetBytes(Encoding.UTF8.GetString(seed).Insert(1,"\"sequence\":1,")),cfg,now));});
        Test("INCOMPLETE_WINDOW",()=>Rejected(()=>Batch.Read(Mutate(seed,o=>o["rows"]=new JsonArray()),cfg,now)));
        Test("NUMERIC_MONEY_REJECTED",()=>Rejected(()=>Batch.Read(Mutate(seed,o=>o["rows"]![0]!["net_amount"]=123.45),cfg,now)));
        Test("WRONG_SUM_REJECTED",()=>Rejected(()=>Batch.Read(Mutate(seed,o=>o["rows"]![0]!["net_amount"]="0"),cfg,now)));
        Test("FUTURE_OBSERVATION",()=>Rejected(()=>Batch.Read(Mutate(seed,o=>o["observed_at"]=Guard.UtcText(now.AddMinutes(6))),cfg,now)));
        Test("FUTURE_WINDOW",()=>Rejected(()=>Batch.Read(Mutate(seed,o=>o["date_to_exclusive"]="2026-02-01"),cfg,now)));
        Test("WRONG_QUERY",()=>Rejected(()=>Batch.Read(Mutate(seed,o=>o["query_sha256"]=new string('0',64)),cfg,now)));
        Test("ZERO_DAY",()=>{
            var zero=Mutate(seed,o=>o["rows"]=JsonSerializer.SerializeToNode(new[]{new SqlReader.Row(Guard.Day(batch.From),0,0,"0","0","0")}));
            Check(Batch.Read(zero,cfg,now).Sequence==1);
        });
        Test("HTTPS_ORIGIN_STRICT",()=>{
            foreach(string origin in new[]{"http://ident-lab.test","https://user@ident-lab.test","https://ident-lab.test/path","https://ident-lab.test:8443","https://ident-lab.test?x=1"})
                Rejected(()=>Settings.Read(Mutate(config,o=>o["https_origin"]=origin)));
        });
        Test("ACK_EXACT_FIELDS",()=>{
            var ack=Ack(batch,cfg,now);batch.VerifyAck(ack,cfg,now);
            foreach(string field in new[]{"source_id","stream_id","batch_id","payload_sha256","dataset","status"})
                Rejected(()=>batch.VerifyAck(Mutate(ack,o=>o[field]="bad"),cfg,now));
            Rejected(()=>batch.VerifyAck(Mutate(ack,o=>o["sequence"]=2),cfg,now));
            Rejected(()=>batch.VerifyAck(Mutate(ack,o=>o["extra"]=1),cfg,now));
        });
        Test("STATUS_PREFIX",()=>{
            Check(Receiver.ParseStatus(Status(cfg,0,now),cfg,now)==0);
            Check(Receiver.ParseStatus(Status(cfg,2,now),cfg,now)==2);
            Rejected(()=>Receiver.ParseStatus(Mutate(Status(cfg,2,now),o=>o["max_sequence"]=3),cfg,now));
            Rejected(()=>Receiver.ParseStatus(Mutate(Status(cfg,2,now),o=>o["mode"]="live"),cfg,now));
        });
        Test("DPAPI_ROUNDTRIP_AND_ENTROPY",()=>{
            byte[] raw=Encoding.UTF8.GetBytes("synthetic-only");byte[] cipher=Dpapi.Transform(raw,cfg.Binding,"test",true);
            Check(!cipher.AsSpan().SequenceEqual(raw));
            Check(Dpapi.Transform(cipher,cfg.Binding,"test",false).AsSpan().SequenceEqual(raw));
            Rejected(()=>Dpapi.Transform(cipher,cfg.Binding,"wrong",false));
            cipher[^1]^=1;Rejected(()=>Dpapi.Transform(cipher,cfg.Binding,"test",false));
        });
        Test("SQL_RESOURCE_FINGERPRINT",()=>{
            using var stream=typeof(SqlReader).Assembly.GetManifestResourceStream("ident.payments.sql")!;
            using var reader=new StreamReader(stream);string sql=reader.ReadToEnd().Replace("\r\n","\n").Replace("\r","\n");
            Check(Guard.Hash(Encoding.UTF8.GetBytes(sql))==Guard.Query);
        });
    }

    private sealed class FakeReceiver(Settings cfg,DateTimeOffset now):IReceiver
    {
        internal bool Offline,DropFirst=true;
        internal long Accepted;
        internal byte[]? First;
        public Task<long> Status(CancellationToken ct)
        {if(Offline)throw new Retry("NETWORK");return Task.FromResult(Accepted);}
        public Task<byte[]> Send(byte[] raw,CancellationToken ct)
        {
            if(Offline)throw new Retry("NETWORK");
            var b=Batch.Read(raw,cfg,now.AddDays(100));
            if(Accepted==0){First=raw.ToArray();Accepted=1;if(DropFirst){DropFirst=false;throw new Retry("LOST_ACK");}}
            else Check(First!.AsSpan().SequenceEqual(raw));
            return Task.FromResult(Ack(b,cfg,now,Accepted==1?"already_accepted":"accepted"));
        }
    }

    private static void JournalTests(Settings cfg,byte[] raw,DateTimeOffset now)
    {
        string path=Path.Combine(PrivateStore.State,"synthetic-journal-tests");
        Check(!Directory.Exists(path));Directory.CreateDirectory(path);
        var dir=new DirectoryInfo(path);var acl=dir.GetAccessControl(AccessControlSections.Owner);acl.SetOwner(PrivateStore.Admin);dir.SetAccessControl(acl);
        try
        {
            Journal.Seed(cfg,path,raw,now);
            Journal j=new(cfg,path,now);
            var fake=new FakeReceiver(cfg,now);
            var collectCalls=0;
            DateTimeOffset clock=now;
            byte[] Collect(DateOnly a,DateOnly b,CancellationToken ct)
            {
                collectCalls++;
                return Batch.Create(cfg,j.LastSequence+1,a,b,new SqlReader.Result(18,10,Guard.UtcText(clock.AddSeconds(-1)),Guard.UtcText(clock),new()));
            }
            Cycle C()=>new(cfg,j,fake,Collect,()=>clock);
            Test("ENCRYPTED_IMMUTABLE_SEED",()=>{Check(j.HasPending&&j.LastSequence==1&&j.PendingBytes(now).AsSpan().SequenceEqual(raw));Check(!File.ReadAllBytes(Directory.GetFiles(path,"packet-*").Single()).AsSpan().SequenceEqual(raw));});
            Test("WRONG_ACK_NO_ADVANCE",()=>{Rejected(()=>j.Ack(Mutate(Ack(j.Last,cfg,now),o=>o["payload_sha256"]=new string('0',64)),now));Check(j.Acked==0);});
            Test("LOST_ACK_RETRY_AFTER_RESTART",()=>{
                Retried(()=>C().Tick(default).GetAwaiter().GetResult());Check(fake.Accepted==1&&j.Acked==0&&collectCalls==0);
                j=new(cfg,path,now);Check(C().Tick(default).GetAwaiter().GetResult()=="ACK_PERSISTED");
                j=new(cfg,path,now);Check(j.Acked==1&&j.LastSequence==1&&fake.Accepted==1);
            });
            Test("REMOTE_ROLLBACK_AND_AHEAD_BLOCKED",()=>{Rejected(()=>j.VerifyRemote(0));Rejected(()=>j.VerifyRemote(2));});
            Test("IDLE_NOT_COLLECTED",()=>{Check(j.Plan(now)==null&&C().Tick(default).GetAwaiter().GetResult()=="IDLE"&&collectCalls==0);});
            clock=now.AddDays(70);
            Test("CATCHUP_MAX_31_DAYS_NO_GAP",()=>{
                var p=j.Plan(clock)!.Value;
                Check(p.From<=j.CoveredEnd&&p.End>j.CoveredEnd&&p.End.DayNumber-p.From.DayNumber<=31);
            });
            Test("OFFLINE_ONE_PENDING_AND_NO_DUPLICATE_COLLECTION",()=>{
                fake.Offline=true;Check(C().Tick(default).GetAwaiter().GetResult()=="PACKET_PERSISTED");
                Check(j.HasPending&&j.LastSequence==2&&collectCalls==1);
                j=new(cfg,path,clock);Retried(()=>C().Tick(default).GetAwaiter().GetResult());
                Check(j.LastSequence==2&&collectCalls==1);
            });
            Test("ORPHAN_TEMP_NOT_TRANSMITTED",()=>{
                string temp=Path.Combine(path,".tmp-"+Guid.NewGuid().ToString("N"));
                PrivateStore.Publish(temp,Dpapi.Transform(raw,cfg.Binding,"orphan",true),adminOwner:true);
                j=new(cfg,path,clock);Check(j.LastSequence==2&&j.Acked==1);
            });
            Test("GAP_OR_MISSING_SEED_STOPS",()=>{
                File.Delete(Directory.GetFiles(path,"packet-0000000000000001.bin").Single());
                Rejected(()=>new Journal(cfg,path,clock));
            });
        }
        finally{Directory.Delete(path,true);}
    }

    private static int Main(string[] args)
    {
        try
        {
            Check(Environment.GetEnvironmentVariable("GITHUB_ACTIONS")=="true"&&Environment.GetEnvironmentVariable("RUNNER_OS")=="Windows"&&Environment.GetEnvironmentVariable("GITHUB_REF")=="refs/heads/test/ident-006c-sender-20261126");
            // Test assembly only: exercise the production NTFS guard against
            // actual fixture ACLs, never add a bypass to the service executable.
            if(args.Length==3&&args[0]=="acl-parent")
            {
                Check(args[2]=="PASS"||args[2]=="UNSAFE_ANCESTOR_ACL");
                string actual="PASS";
                try{PrivateStore.AssertAncestors(args[1]);}
                catch(Blocked e){actual=e.Message;}
                Check(actual==args[2]);
                Console.WriteLine("RUNTIME_ANCESTOR="+actual+"; EXPECTED=PASS");
                return 0;
            }
            if(args.Length==1&&args[0]=="acl-layout")
            {
                PrivateStore.VerifyDirectoryLayout();
                Console.WriteLine("RUNTIME_DIRECTORY_LAYOUT=PASS");
                return 0;
            }
            Unit();
            if(args.Length==2&&args[0]=="lab-seed")
            {
                string lab=Path.GetFullPath(args[1]);
                byte[] pin=File.ReadAllBytes(Path.Combine(lab,"fixture.cer"));
                var now=DateTimeOffset.UtcNow;
                byte[] raw=Seed(now);
                byte[] token=Encoding.ASCII.GetBytes(Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant());
                byte[] cfgRaw=Provision.MakeConfig(Template(Guard.Hash(pin)),raw,token);
                var cfg=Settings.Read(cfgRaw);
                JournalTests(cfg,raw,now);
                using var pw=new SecureString();
                foreach(char ch in "synthetic-only-password")pw.AppendChar(ch);
                pw.MakeReadOnly();
                Provision.Write(cfgRaw,raw,pin,pw,token);
                File.WriteAllBytes(Path.Combine(lab,"seed.json"),raw);
                File.WriteAllBytes(Path.Combine(lab,"token.txt"),token);
                File.WriteAllBytes(Path.Combine(lab,"config.json"),cfgRaw);
                CryptographicOperations.ZeroMemory(token);
                Test("ADMIN_PROVISION_AND_LOCAL_CHECK",()=>Check(Provision.CheckLocal().Source==Source));
            }
            else Check(args.Length==0);
            Console.WriteLine("SYNTHETIC_TESTS="+count+"/"+count+" PASS");
            return 0;
        }
        catch(Exception e){Console.Error.WriteLine("TEST_FAILURE="+e.GetType().Name+"; "+e.Message);return 1;}
    }
}
