// Source-only candidate. The parent PowerShell collector owns the outbox and
// publishes the unchanged v1 contract; this worker has no file output or upload.
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
    private sealed record Config(string ServerName, int Port, string Database,
        string SqlUser, string CertificatePath, string CertificateSha256,
        string DateFrom, string DateToExclusive, string QuerySha256, bool CheckOnly);
    private sealed record Row(string date, long records_count, long paying_patients,
        string positive_amount, string negative_amount, string net_amount);
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

    private static (int precision, int scale) CheckSchema(SqlCommand cmd)
    {
        cmd.CommandText = ReadSql("ident.schema.sql");
        var expected = new Dictionary<string, string>(StringComparer.Ordinal) {
            ["DatePayment"] = "date", ["ID_Patients"] = "int", ["Payment"] = "decimal" };
        int precision = 0, scale = 0;
        using var reader = cmd.ExecuteReader();
        while (reader.Read())
        {
            var column = reader.GetString(0);
            Require(expected.Remove(column, out var type) && reader.GetString(1) == type &&
                !reader.GetBoolean(2), "INCOMPATIBLE_SOURCE_SCHEMA");
            if (column == "Payment")
            {
                precision = reader.GetByte(3);
                scale = reader.GetByte(4);
            }
        }
        Require(expected.Count == 0 && precision is >= 1 and <= 38 &&
            scale >= 0 && scale <= precision && !reader.NextResult(),
            "INCOMPATIBLE_SOURCE_SCHEMA");
        return (precision, scale);
    }

    private static int Main(string[] args)
    {
        try
        {
            Require(OperatingSystem.IsWindows() && Environment.Is64BitProcess,
                "WINDOWS_X64_REQUIRED");
            Require(args.Length == 1 && args[0].Length <= 32768, "CONFIG_REQUIRED");
            var cfg = JsonSerializer.Deserialize<Config>(Convert.FromBase64String(args[0]))
                ?? throw new Stop("CONFIG_INVALID");
            Require(Regex.IsMatch(cfg.ServerName ?? "", "^[A-Za-z0-9][A-Za-z0-9.\\-]*$") &&
                cfg.Port is >= 1 and <= 65535 && !string.IsNullOrWhiteSpace(cfg.Database) &&
                !string.IsNullOrWhiteSpace(cfg.SqlUser), "CONFIG_INVALID");
            var start = DateTime.ParseExact(cfg.DateFrom, "yyyy-MM-dd", CultureInfo.InvariantCulture);
            var end = DateTime.ParseExact(cfg.DateToExclusive, "yyyy-MM-dd", CultureInfo.InvariantCulture);
            Require((end - start).Days is >= 1 and <= 31, "INVALID_DATE_WINDOW");
            var sql = ReadSql("ident.payments.sql");
            var queryHash = Hash(Encoding.UTF8.GetBytes(sql));
            Require(queryHash == cfg.QuerySha256, "QUERY_FINGERPRINT_MISMATCH");
            using var pin = OpenVerifiedPin(cfg);
            var builder = new SqlConnectionStringBuilder
            {
                DataSource = $"tcp:{cfg.ServerName},{cfg.Port}",
                InitialCatalog = cfg.Database,
                Encrypt = SqlConnectionEncryptOption.Mandatory,
                TrustServerCertificate = false,
                ServerCertificate = cfg.CertificatePath,
                ConnectTimeout = 10,
                ConnectRetryCount = 0,
                Pooling = false,
                ApplicationName = "IDENT-004 read-only pinned pilot"
            };
            using var pw = ReadPassword();
            using var conn = new SqlConnection(builder.ConnectionString, new SqlCredential(cfg.SqlUser, pw));
            // Driver checks an exact server certificate during TLS, before SQL authentication.
            // No retry with a weaker validation policy, no second connection.
            conn.Open();
            Require(string.Equals(conn.Database, cfg.Database, StringComparison.Ordinal),
                "DATABASE_MISMATCH");
            using var cmd = conn.CreateCommand();
            cmd.CommandTimeout = 30;
            CheckRights(cmd);
            var (precision, scale) = CheckSchema(cmd);
            if (cfg.CheckOnly)
            {
                Console.Out.Write(JsonSerializer.Serialize(new {
                    query_sha256 = queryHash, payment_precision = precision,
                    payment_scale = scale, check_only = true
                }));
                return 0;
            }
            cmd.CommandText = sql;
            cmd.Parameters.Add("@start_date", SqlDbType.Date).Value = start;
            cmd.Parameters.Add("@end_date_exclusive", SqlDbType.Date).Value = end;
            var rows = new List<Row>();
            var dates = new HashSet<string>(StringComparer.Ordinal);
            var started = DateTime.UtcNow;
            using (var reader = cmd.ExecuteReader())
            {
                while (reader.Read())
                {
                    Require(reader.FieldCount == 6 && rows.Count < (end - start).Days,
                        "INVALID_SQL_RESULT");
                    var date = reader.GetString(0);
                    var day = DateTime.ParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture);
                    Require(day >= start && day < end && dates.Add(date), "INVALID_SQL_RESULT");
                    rows.Add(new Row(date, reader.GetInt64(1), reader.GetInt64(2),
                        reader.GetString(3), reader.GetString(4), reader.GetString(5)));
                }
                Require(!reader.NextResult(), "UNEXPECTED_EXTRA_RESULT_SET");
            }
            var finished = DateTime.UtcNow;
            // Only the private parent process receives stdout. No password, individual rows,
            // connection string, raw exception or certificate bytes are sent.
            Console.Out.Write(JsonSerializer.Serialize(new {
                query_sha256 = queryHash, payment_precision = precision, payment_scale = scale,
                check_only = false, query_started_at = Utc(started), observed_at = Utc(finished), rows
            }));
            return 0;
        }
        catch (Stop e)
        {
            Console.Error.WriteLine("PINNED_SQL=STOP; REASON=" + e.Message);
        }
        catch (SqlException e)
        {
            Console.Error.WriteLine("PINNED_SQL=STOP; REASON=SQL_CONNECTION_OR_QUERY; NUMBER=" + e.Number);
        }
        catch (Exception)
        {
            Console.Error.WriteLine("PINNED_SQL=STOP; REASON=LOCAL_VALIDATION_OR_RUNTIME");
        }
        return 1;
    }
}
