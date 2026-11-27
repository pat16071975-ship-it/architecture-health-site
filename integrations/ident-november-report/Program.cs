using System.Data;
using System.Globalization;
using System.Security;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Data.SqlClient;

internal static class Program
{
    private sealed record Config(string ServerName, int Port, string Database, string SqlUser, string CertificatePath, string CertificateSha256, string Mode, string? QueryPath, string? QuerySha256);
    private sealed class Stop(string code) : Exception(code) { }
    private static void Require(bool value, string code)
    {
        if (!value) throw new Stop(code);
    }

    private static string Hex(byte[] bytes) => Convert.ToHexString(bytes).ToLowerInvariant();
    private static string Hash(byte[] bytes) => Hex(SHA256.HashData(bytes));
    private static string ReadSql(string name)
    {
        using var stream = typeof(Program).Assembly.GetManifestResourceStream(name)
            ?? throw new Stop("SQL_RESOURCE_MISSING");
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

    private static SecureString ReadPassword()
    {
        Require(!Console.IsInputRedirected, "INTERACTIVE_CONSOLE_REQUIRED");
        var password = new SecureString();
        try
        {
            Console.Error.Write("SQL password: ");
            while (true)
            {
                var key = Console.ReadKey(intercept: true);
                if (key.Key == ConsoleKey.Enter) break;
                if (key.Key == ConsoleKey.Escape) throw new Stop("PASSWORD_CANCELLED");
                if (key.Key == ConsoleKey.Backspace)
                {
                    if (password.Length > 0) password.RemoveAt(password.Length - 1);
                    continue;
                }
                if (char.IsControl(key.KeyChar)) continue;
                Require(password.Length < 128, "PASSWORD_TOO_LONG");
                password.AppendChar(key.KeyChar);
            }
            Console.Error.WriteLine();
            Require(password.Length > 0, "PASSWORD_REQUIRED");
            password.MakeReadOnly();
            return password;
        }
        catch
        {
            password.Dispose();
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


    // One-time local tool. Only reviewed and independently SHA-pinned aggregate SQL.
    // Lexical checks are sanity gates, not an SQL sandbox. Never deploy as an endpoint.
    private static void ValidateQuery(string sql)
    {
        Require(Regex.IsMatch(sql, @"\A(?:SELECT|WITH)\s", RegexOptions.IgnoreCase)
          && !Regex.IsMatch(sql, @"[;#]|--|/\*|\*/|\b(?:INSERT|UPDATE|DELETE|MERGE|ALTER|CREATE|DROP|TRUNCATE|EXEC|EXECUTE|GRANT|DENY|REVOKE|INTO|OPENROWSET|OPENDATASOURCE|OPENQUERY|BACKUP|RESTORE|DBCC|BULK|WAITFOR|KILL|USE|SET|OPTION|NOLOCK|READUNCOMMITTED|UPDLOCK|XLOCK|HOLDLOCK)\b", RegexOptions.IgnoreCase),
          "QUERY_NOT_APPROVED_SELECT");
    }
    private static string ApprovedQuery(Config cfg, out FileStream file)
    {
        Require(cfg.QuerySha256 is not null && Regex.IsMatch(cfg.QuerySha256, "^[0-9a-f]{64}$"), "QUERY_HASH_REQUIRED");
        Require(cfg.QueryPath is not null && Path.IsPathFullyQualified(cfg.QueryPath)
          && string.Equals(Path.GetDirectoryName(cfg.QueryPath),Path.GetDirectoryName(cfg.CertificatePath),StringComparison.OrdinalIgnoreCase)
          && (File.GetAttributes(cfg.QueryPath) & FileAttributes.ReparsePoint)==0, "QUERY_PATH");
        file=new FileStream(cfg.QueryPath!,FileMode.Open,FileAccess.Read,FileShare.Read);
        try {
            Require(file.Length is >0 and <=65536,"QUERY_SIZE");
            var bytes=new byte[(int)file.Length];file.ReadExactly(bytes);
            Require(Hash(bytes)==cfg.QuerySha256,"QUERY_HASH_MISMATCH");
            var sql=new UTF8Encoding(false,true).GetString(bytes).TrimStart('\uFEFF').Trim();
            ValidateQuery(sql);return sql;
        }catch{file.Dispose();throw;}
    }
    private static string SafeText(object value,int limit)
    {
        var s=Convert.ToString(value,CultureInfo.InvariantCulture)??"";
        Require(s.Length<=limit&&!s.Any(char.IsControl),"OUTPUT_TEXT");return s;
    }
    private static void ReadRows(SqlCommand cmd,string sql,string label,List<string[]> rows)
    {
        cmd.CommandText=sql;
        using var r=cmd.ExecuteReader();
        while(r.Read()){
            Require(rows.Count<5500,"METADATA_LIMIT");
            var row=new List<string>{label};
            for(int i=0;i<r.FieldCount;i++)row.Add(SafeText(r[i],128));
            rows.Add(row.ToArray());
        }
        Require(!r.NextResult(),"EXTRA_RESULTS");
    }
    private static void Metadata(SqlCommand cmd,List<string[]> rows)
    {
        ReadRows(cmd,"SELECT s.name,t.name FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id WHERE t.is_ms_shipped=0 ORDER BY s.name,t.name","TABLE",rows);
        ReadRows(cmd,"""
            SELECT s.name,t.name,c.name,ty.name,c.max_length,c.precision,c.scale,c.is_nullable,
              CASE WHEN EXISTS(SELECT 1 FROM sys.indexes i JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id WHERE i.object_id=t.object_id AND i.is_primary_key=1 AND ic.column_id=c.column_id) THEN 1 ELSE 0 END,
              c.is_computed
            FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
            JOIN sys.columns c ON c.object_id=t.object_id JOIN sys.types ty ON ty.user_type_id=c.user_type_id
            WHERE t.is_ms_shipped=0 AND
            (t.name IN ('CurrentTimeTable','Receptions','RenderedServicesJournal','Times','Armchairs','Staffs','PaymentsIn','Departments','Specialities','Specializations','Services','Invoices','ServiceGroups')
              OR t.name LIKE '%Reception%' OR t.name LIKE '%TimeTable%' OR t.name LIKE '%Schedule%' OR t.name LIKE '%RenderedService%' OR t.name LIKE '%Department%' OR t.name LIKE '%Specialit%' OR t.name LIKE '%Staff%')
            ORDER BY s.name,t.name,c.column_id
            ""","COL",rows);
        ReadRows(cmd,"""
            SELECT OBJECT_SCHEMA_NAME(f.parent_object_id),OBJECT_NAME(f.parent_object_id),pc.name,
                   OBJECT_SCHEMA_NAME(f.referenced_object_id),OBJECT_NAME(f.referenced_object_id),rc.name
            FROM sys.foreign_key_columns f
            JOIN sys.columns pc ON pc.object_id=f.parent_object_id AND pc.column_id=f.parent_column_id
            JOIN sys.columns rc ON rc.object_id=f.referenced_object_id AND rc.column_id=f.referenced_column_id
            WHERE OBJECT_NAME(f.parent_object_id) IN ('CurrentTimeTable','Receptions','RenderedServicesJournal','Times','Armchairs','Staffs','Invoices','Services','PaymentsIn')
               OR OBJECT_NAME(f.parent_object_id) LIKE '%Reception%'
               OR OBJECT_NAME(f.parent_object_id) LIKE '%TimeTable%'
               OR OBJECT_NAME(f.parent_object_id) LIKE '%RenderedService%'
            ORDER BY f.parent_object_id,f.constraint_object_id,f.constraint_column_id
            ""","FK",rows);
    }
    private static void Aggregate(SqlCommand cmd,string sql,List<string[]> rows)
    {
        cmd.CommandText=sql;
        using var r=cmd.ExecuteReader();
        string[] expected={"metric","staff","value"};
        Require(r.FieldCount==expected.Length&&Enumerable.Range(0,expected.Length).All(i=>r.GetName(i)==expected[i]),"AGGREGATE_COLUMNS");
        while(r.Read()){
            Require(rows.Count<1000&&!r.IsDBNull(0)&&!r.IsDBNull(1),"AGGREGATE_LIMIT_OR_NULL");
            var metric=SafeText(r[0],60);
            Require(Regex.IsMatch(metric,@"\A[a-z][a-z0-9_]{0,59}\z"),"METRIC_NAME");
            var staff=SafeText(r[1],120);
            var value="";
            if(!r.IsDBNull(2)){
                Require(new[]{typeof(decimal),typeof(long),typeof(int),typeof(short),typeof(byte)}.Contains(r.GetFieldType(2)),"AGGREGATE_NUMERIC_TYPE");
                value=Convert.ToString(r[2],CultureInfo.InvariantCulture)??"";
            }
            rows.Add(new[]{metric,staff,value});
        }
        Require(!r.NextResult(),"EXTRA_RESULTS");
    }
    private static int Main(string[] args)
    {
        try{
            Require(OperatingSystem.IsWindows()&&Environment.Is64BitProcess,"WINDOWS_X64_REQUIRED");
            if(args.Length==1&&args[0]=="--self-test"){
                ValidateQuery("SELECT 'hours' AS metric, 'Test' AS staff, CAST(1 AS bigint) AS value");
                ValidateQuery("WITH a AS (SELECT 1 AS n) SELECT 'count' AS metric, 'Test' AS staff, n AS value FROM a");
                foreach(var sql in new[]{"DELETE FROM dbo.Receptions","SELECT 1; UPDATE dbo.Receptions SET ID=1","SELECT * INTO test FROM dbo.Receptions","SELECT * FROM OPENROWSET('a','b','c')","SELECT 1 --comment"}){
                    try{ValidateQuery(sql);}catch(Stop){continue;}throw new Stop("SELF_TEST_GATE");
                }
                Console.WriteLine("QUERY_GATE_SELF_TEST=PASS; SQL_CONNECTION=NONE");return 0;
            }
            Require(args.Length==1&&args[0].Length<=32768,"CONFIG_REQUIRED");
            var cfg=JsonSerializer.Deserialize<Config>(Convert.FromBase64String(args[0]))??throw new Stop("CONFIG_INVALID");
            Require(Regex.IsMatch(cfg.ServerName??"", @"\A[A-Za-z0-9][A-Za-z0-9.\-]*\z")&&cfg.Port is >=1 and <=65535
              &&!string.IsNullOrWhiteSpace(cfg.Database)&&!string.IsNullOrWhiteSpace(cfg.SqlUser)&&cfg.Mode is "metadata" or "aggregate","CONFIG_INVALID");
            using var pin=OpenVerifiedPin(cfg);
            FileStream? queryLock=null;
            try{
                string? query=null;
                if(cfg.Mode=="aggregate")query=ApprovedQuery(cfg,out queryLock);
                var builder=new SqlConnectionStringBuilder{DataSource=$"tcp:{cfg.ServerName},{cfg.Port}",InitialCatalog=cfg.Database,
                    Encrypt=SqlConnectionEncryptOption.Mandatory,TrustServerCertificate=false,ServerCertificate=cfg.CertificatePath,
                    ConnectTimeout=10,ConnectRetryCount=0,Pooling=false,ApplicationName="IDENT one-off management research read-only"};
                using var pw=ReadPassword();
                using var conn=new SqlConnection(builder.ConnectionString,new SqlCredential(cfg.SqlUser,pw));
                conn.Open();
                Require(string.Equals(conn.Database,cfg.Database,StringComparison.Ordinal),"DATABASE_MISMATCH");
                using var cmd=conn.CreateCommand();cmd.CommandTimeout=30;
                CheckRights(cmd);
                var rows=new List<string[]>();
                if(cfg.Mode=="metadata")Metadata(cmd,rows);else Aggregate(cmd,query!,rows);
                var json=JsonSerializer.Serialize(new{mode=cfg.Mode,tls="PINNED",read_only="PASS",observed_at=Utc(DateTime.UtcNow),query_sha256=cfg.QuerySha256,rows});
                Require(Encoding.UTF8.GetByteCount(json)<=524288,"OUTPUT_LIMIT");
                Console.OutputEncoding=new UTF8Encoding(false);Console.WriteLine(json);return 0;
            }finally{queryLock?.Dispose();}
        }catch(Stop e){Console.Error.WriteLine("IDENT_REPORT=STOP; REASON="+e.Message);}
        catch(SqlException e){Console.Error.WriteLine("IDENT_REPORT=STOP; SQL_ERROR_NUMBER="+e.Number);}
        catch(Exception){Console.Error.WriteLine("IDENT_REPORT=STOP; REASON=LOCAL_VALIDATION_OR_RUNTIME");}
        return 1;
    }
}
