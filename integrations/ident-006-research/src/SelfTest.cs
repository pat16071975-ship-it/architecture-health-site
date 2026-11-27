using System.Text;
using System.Text.Json;
using System.Security.Cryptography;
internal static class SelfTest
{
    private static int tests;
    private static void Check(bool b){if(!b)throw new Exception("SELF_TEST");tests++;}
    private static void Reject(Action action)
    {
        try{action();}catch(Blocked){tests++;return;}throw new Exception("SELF_TEST_EXPECTED_REJECTION");
    }
    internal static void Run()
    {
        var w=ReportWindow.Parse("2026-10-01","2026-11-01",1);
        Check(w.To.DayNumber-w.From.DayNumber==31);
        Reject(()=>ReportWindow.Parse("2026-10-01","2026-11-02",1));
        Reject(()=>ReportWindow.Parse("2026-10-01","2026-10-01",1));
        Reject(()=>ReportWindow.Parse("2026-10-01","2026-09-01",1));
        Reject(()=>ReportWindow.Parse("2026-10-01' OR 1=1","2026-11-01",1));
        Reject(()=>ReportWindow.Parse("2026-10-01","2026-11-01",0));
        Reject(()=>ReportWindow.Parse("2026-10-1","2026-11-01",1));
        Check(ReportWindow.Parse("2026-11-01","2026-12-01",null).StaffId is null);
        byte[] config=Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new{
            source_id="00000000-0000-4000-8000-000000000001",source_timezone="Asia/Novosibirsk",
            sql_host="127.0.0.1",sql_port=15433,sql_database="Synthetic",sql_user="lab_readonly",
            certificate_sha256=new string('a',64)
        }));
        var cfg=ResearchSettings.Read(config);Check(cfg.Host=="127.0.0.1"&&cfg.Port==15433);
        Reject(()=>ResearchSettings.Read(Encoding.UTF8.GetBytes(Encoding.UTF8.GetString(config).Replace("\"127.0.0.1\"","\"host;Password=secret\""))));
        var pw=Encoding.Unicode.GetBytes("SyntheticOnly");
        var sealedPw=Dpapi.Transform(pw,cfg.Binding,"sql-password",true);
        var restored=Dpapi.Transform(sealedPw,cfg.Binding,"sql-password",false);
        Check(restored.AsSpan().SequenceEqual(pw));CryptographicOperations.ZeroMemory(restored);
        Reject(()=>Dpapi.Transform(sealedPw,cfg.Binding,"other-purpose",false));
        CryptographicOperations.ZeroMemory(pw);CryptographicOperations.ZeroMemory(sealedPw);
        Check(Output.HoursColumns.Distinct().Count()==15);
        Check(Output.PrimaryColumns.Distinct().Count()==14);
        Check(Output.Categories.Length==4);
        Reject(()=>Output.Validate(new(),false,1));
        foreach(var name in new[]{"ident.guard.sql","ident.hours.sql","ident.primary.sql"}){
            using var stream=typeof(SelfTest).Assembly.GetManifestResourceStream(name);
            Check(stream is not null&&stream.Length>100);
        }
        Console.WriteLine($"RESEARCH_SELF_TEST={tests}/{tests} PASS; SQL_CONNECTION=NONE; REAL_DATA=NONE; TUNNEL=NONE");
    }
}
