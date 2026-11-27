using System.Data;
using System.Globalization;
using System.Security;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Data.SqlClient;

internal static class ResearchSql
{
    internal sealed record Config(string ServerName,int Port,string Database,string SqlUser,string CertificatePath,string CertificateSha256);
    private static void Require(bool value, string code) => Guard.Require(value, code);
    private static string Hex(byte[] bytes) => Convert.ToHexString(bytes).ToLowerInvariant();
    private static string Hash(byte[] bytes) => Hex(SHA256.HashData(bytes));
    private static string ReadSql(string name)
    {
        using var stream = typeof(ResearchSql).Assembly.GetManifestResourceStream(name)
            ?? throw new Blocked("SQL_RESOURCE_MISSING");
        using var reader = new StreamReader(stream, new UTF8Encoding(false, true));
        return reader.ReadToEnd().Replace("\r\n", "\n").Replace("\r", "\n");
    }
    private static string Utc(DateTime utc) =>
        utc.ToString("yyyy-MM-ddTHH:mm:ss.ffffff'Z'", CultureInfo.InvariantCulture);

    // Explicitly provisioned DER certificate and independently approved hash.
    // This does not enroll, fetch or learn a certificate from the SQL endpoint.
    private static FileStream OpenVerifiedPin(Config cfg)
    {
        Require(Regex.IsMatch(cfg.CertificateSha256 ?? "", "^[0-9a-f]{64}$"),
            "PIN_SHA256_REQUIRED");
        Require(!string.IsNullOrWhiteSpace(cfg.CertificatePath) &&
            Path.IsPathFullyQualified(cfg.CertificatePath) &&
            string.Equals(Path.GetFileName(cfg.CertificatePath), "server.cer", StringComparison.Ordinal),
            "PIN_PATH_INVALID");
        Require((File.GetAttributes(cfg.CertificatePath) & FileAttributes.ReparsePoint) == 0,
            "PIN_REPARSE_POINT");
        // Keep the exact file locked against writes/deletion during all SQL operations.
        var file = new FileStream(cfg.CertificatePath, FileMode.Open, FileAccess.Read, FileShare.Read);
        try
        {
            Require(file.Length is > 0 and <= 65536, "PIN_FILE_SIZE");
            var bytes = new byte[(int)file.Length];
            file.ReadExactly(bytes);
            Require(Hash(bytes) == cfg.CertificateSha256, "PIN_SHA256_MISMATCH");
            using var cert = X509CertificateLoader.LoadCertificate(bytes);
            Require(cert.RawData.AsSpan().SequenceEqual(bytes) && !cert.HasPrivateKey,
                "PIN_NOT_EXACT_PUBLIC_DER");
            var now = DateTime.UtcNow;
            Require(now >= cert.NotBefore.ToUniversalTime() &&
                now <= cert.NotAfter.ToUniversalTime(), "PIN_EXPIRED_OR_NOT_YET_VALID");
            return file;
        }
        catch
        {
            file.Dispose();
            throw;
        }
    }

    private static void CheckRights(SqlCommand cmd)
    {
        cmd.CommandText = "SELECT DB_NAME(), HAS_PERMS_BY_NAME(N'dbo.PaymentsIn',N'OBJECT',N'SELECT')";
        using (var r = cmd.ExecuteReader())
        {
            Require(r.Read() && r.FieldCount == 2 && !r.IsDBNull(0) &&
                string.Equals(r.GetString(0), cmd.Connection!.Database, StringComparison.Ordinal) &&
                !r.IsDBNull(1) && r.GetInt32(1) == 1 && !r.Read() && !r.NextResult(),
                "SOURCE_PREFLIGHT_FAILED");
        }
        cmd.CommandText = ReadSql("ident.guard.sql");
        var checks = new HashSet<string>(StringComparer.Ordinal) {
            "identity", "roles", "server_effective", "database_effective",
            "server_grants", "database_grants", "ownership", "payments_effective" };
        using var reader = cmd.ExecuteReader();
        while (reader.Read())
        {
            Require(reader.FieldCount == 2 && !reader.IsDBNull(0) && !reader.IsDBNull(1) &&
                checks.Remove(reader.GetString(0)), "SQL_READONLY_INVALID_RESULT");
            Require(reader.GetInt32(1) == 1, "SQL_READONLY_GUARD_FAILED");
        }
        Require(checks.Count == 0 && !reader.NextResult(), "SQL_READONLY_INCOMPLETE_RESULT");
    }


    private static readonly Dictionary<string,string[]> CommonColumns=new(StringComparer.Ordinal){
        ["Staffs"]=new[]{"ID_Persons:int","Archive:bit"},
        ["Persons"]=new[]{"ID:int","Surname:nvarchar","Name:nvarchar","Patronymic:nvarchar"},
        ["Receptions"]=new[]{"ID:int","ID_Staffs:int","ID_Patients:int","PlanStart:datetime","PlanEnd:datetime","ReceptionStarted:datetime","ReceptionEnded:datetime","PatientAppeared:datetime","ReceptionCanceled:datetime","ID_ReceptionCancelReasons:int"},
        ["Orders"]=new[]{"ID:int","ID_Receptions:int"},
        ["RenderedServicesJournal"]=new[]{"ID_Staffs:int","ID_Orders:int","DateTimeOrder:datetime","AmountWithDiscount:decimal"}
    };
    private static readonly Dictionary<string,string[]> HoursColumns=new(StringComparer.Ordinal){
        ["CurrentTimeTable"]=new[]{"ID_Staffs:int","ID_Times:tinyint","DateOfWork:date","IsWorkingTime:bit","IsHeaderCell:bit","ID_TimeReserves:int"},
        ["Times"]=new[]{"ID:tinyint","TimeStartValue:time","TimeEndValue:time"}
    };
    private static void CheckSchema(SqlCommand cmd,bool hours)
    {
        var expected=CommonColumns.Concat(hours?HoursColumns:new Dictionary<string,string[]>()).ToDictionary(x=>x.Key,x=>x.Value.ToHashSet(StringComparer.Ordinal),StringComparer.Ordinal);
        foreach(var (name,columns) in expected)
        {
            cmd.Parameters.Clear();
            cmd.CommandText=@"SELECT c.name,t.name
FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id
JOIN sys.columns c ON c.object_id=o.object_id
JOIN sys.types t ON t.user_type_id=c.user_type_id
WHERE s.name=N'dbo' AND o.name=@name AND o.type=@kind";
            cmd.Parameters.Add("@name",SqlDbType.NVarChar,128).Value=name;
            cmd.Parameters.Add("@kind",SqlDbType.Char,2).Value=name=="RenderedServicesJournal"?"V":"U";
            using(var r=cmd.ExecuteReader())
            {
                while(r.Read())columns.Remove(r.GetString(0)+":"+r.GetString(1));
                Require(columns.Count==0&&!r.NextResult(),"SOURCE_SCHEMA_MISMATCH");
            }
            cmd.Parameters.Clear();
            cmd.CommandText="SELECT HAS_PERMS_BY_NAME(@object,N'OBJECT',N'SELECT'),COUNT(*) FROM sys.fn_my_permissions(@object,N'OBJECT') WHERE permission_name NOT IN (N'SELECT',N'VIEW DEFINITION')";
            cmd.Parameters.Add("@object",SqlDbType.NVarChar,256).Value="dbo."+name;
            using(var r=cmd.ExecuteReader())
                Require(r.Read()&&!r.IsDBNull(0)&&r.GetInt32(0)==1&&r.GetInt32(1)==0&&!r.Read()&&!r.NextResult(),"OBJECT_READONLY_GUARD");
        }
        cmd.Parameters.Clear();
    }
    internal static object Collect(ResearchSettings settings,string resource,ReportWindow window,SecureString pw,CancellationToken ct)
    {
        bool hours=resource=="ident.hours.sql";
        Require(hours||resource=="ident.primary.sql","QUERY_NOT_IN_CATALOG");
        Require(hours?window.StaffId is null:window.StaffId is >0,"QUERY_ARGUMENT_MISMATCH");
        var cfg=new Config(settings.Host,settings.Port,settings.Database,settings.User,PrivateStore.Pin,settings.PinHash);
        using var pin=OpenVerifiedPin(cfg);
        var builder=new SqlConnectionStringBuilder{
            DataSource=$"tcp:{cfg.ServerName},{cfg.Port}",InitialCatalog=cfg.Database,
            Encrypt=SqlConnectionEncryptOption.Mandatory,TrustServerCertificate=false,ServerCertificate=cfg.CertificatePath,
            ConnectTimeout=10,ConnectRetryCount=0,Pooling=false,ApplicationName="IDENT research pinned read-only"
        };
        using var conn=new SqlConnection(builder.ConnectionString,new SqlCredential(cfg.SqlUser,pw));
        conn.OpenAsync(ct).GetAwaiter().GetResult();
        Require(conn.Database==cfg.Database,"DATABASE_MISMATCH");
        using var cmd=conn.CreateCommand();
        cmd.CommandTimeout=20;
        using var cancel=ct.Register(()=>{try{cmd.Cancel();}catch(Exception){}});
        CheckRights(cmd);ct.ThrowIfCancellationRequested();
        CheckSchema(cmd,hours);ct.ThrowIfCancellationRequested();
        cmd.CommandText="SELECT GETDATE()";
        var before=DateTime.UtcNow;
        var observed=DateTime.SpecifyKind((DateTime)(cmd.ExecuteScalar()??throw new Blocked("SOURCE_CLOCK")),DateTimeKind.Unspecified);
        var local=TimeZoneInfo.ConvertTimeFromUtc(DateTime.UtcNow,settings.Zone);
        Require(Math.Abs((observed-local).TotalSeconds)<=120,"SOURCE_CLOCK_OR_TIMEZONE_MISMATCH");
        var from=window.From.ToDateTime(TimeOnly.MinValue);
        var to=window.To.ToDateTime(TimeOnly.MinValue);
        var cutoff=observed<to?observed:to;
        if(cutoff<from)cutoff=from;
        var sql=ReadSql(resource);
        cmd.CommandText=sql;
        cmd.Parameters.Add("@date_from",SqlDbType.DateTime).Value=from;
        cmd.Parameters.Add("@date_to",SqlDbType.DateTime).Value=to;
        cmd.Parameters.Add("@fact_end",SqlDbType.DateTime).Value=cutoff;
        cmd.Parameters.Add("@observed",SqlDbType.DateTime).Value=observed;
        if(window.StaffId is int staffId)cmd.Parameters.Add("@staff_id",SqlDbType.Int).Value=staffId;
        var rows=new List<Dictionary<string,object>>();
        var expected=hours?Output.HoursColumns:Output.PrimaryColumns;
        using(var r=cmd.ExecuteReader())
        {
            Require(r.FieldCount==expected.Length&&Enumerable.Range(0,expected.Length).All(i=>r.GetName(i)==expected[i]),"RESULT_SCHEMA");
            while(r.Read()){
                Require(rows.Count<(hours?250:4),"RESULT_LIMIT");
                var row=new Dictionary<string,object>(StringComparer.Ordinal);
                for(int i=0;i<expected.Length;i++){
                    Require(!r.IsDBNull(i),"RESULT_NULL");
                    var k=expected[i];
                    object value=r.GetValue(i);
                    if(k=="staff_name"){
                        Require(value is string s&&s.Length is >0 and <=120&&!s.Any(char.IsControl),"STAFF_NAME_INVALID");
                    }else if(k=="category"){
                        Require(value is string category&&Output.Categories.Contains(category),"CATEGORY_INVALID");
                    }else if(k=="archived"){
                        Require(value is bool,"RESULT_TYPE");
                    }else if(k.StartsWith("revenue",StringComparison.Ordinal)){
                        Require(value is decimal,"RESULT_TYPE");
                        value=((decimal)value).ToString("0.000000",CultureInfo.InvariantCulture);
                    }else{
                        Require(value is long or int,"RESULT_TYPE");
                        var n=Convert.ToInt64(value,CultureInfo.InvariantCulture);
                        Require(n>=0&&n<=1000000000L,"RESULT_NUMBER");
                        value=n;
                    }
                    row.Add(k,value);
                }
                rows.Add(row);
            }
            Require(!r.NextResult(),"EXTRA_RESULT_SET");
        }
        Output.Validate(rows,hours,window.StaffId);
        ct.ThrowIfCancellationRequested();
        return new {
            source="IDENT",source_id=settings.Source,source_timezone=settings.ZoneName,
            date_from=Guard.Day(window.From),date_to_exclusive=Guard.Day(window.To),
            source_observed_local=observed.ToString("yyyy-MM-ddTHH:mm:ss.fff",CultureInfo.InvariantCulture),
            fact_end_exclusive_local=cutoff.ToString("yyyy-MM-ddTHH:mm:ss.fff",CultureInfo.InvariantCulture),
            query_started_utc=Utc(before),query_finished_utc=Utc(DateTime.UtcNow),
            read_only=true,tls="exact_pinned_certificate",query_sha256=Hash(Encoding.UTF8.GetBytes(sql)),
            coverage="current_database_state_not_historical_snapshot",rows
        };
    }
}
