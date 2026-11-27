using System.Data;
using System.Diagnostics;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Data.SqlClient;

// This executable is a laboratory only. It does not call ResearchSql.Collect,
// provision PrivateStore, accept a connection string, or contact IDENT.
internal static class SqlLab
{
    private static int assertions;
    private static readonly DateTime From = D("2026-11-01T00:00:00");
    private static readonly DateTime To = D("2026-12-01T00:00:00");
    private static readonly DateTime Cutoff = D("2026-11-15T12:00:00");
    private static DateTime D(string value) => DateTime.ParseExact(value, "yyyy-MM-ddTHH:mm:ss", CultureInfo.InvariantCulture);
    private static void Assert(bool condition, string name)
    {
        if (!condition) throw new InvalidOperationException("SQL_LAB_ASSERTION_FAILED: " + name);
        assertions++;
    }
    private static long N(Dictionary<string, object> row, string key) => (long)row[key];
    private static decimal M(Dictionary<string, object> row, string key) => decimal.Parse((string)row[key], CultureInfo.InvariantCulture);
    private static Dictionary<string, object> Staff(List<Dictionary<string, object>> rows, int id) => rows.Single(r => N(r, "staff_id") == id);
    private static Dictionary<string, object> Category(List<Dictionary<string, object>> rows, string category) => rows.Single(r => (string)r["category"] == category);

    internal static async Task RunAsync()
    {
        Assert(OperatingSystem.IsWindows(), "Windows LocalDB laboratory required");
        string localDb = FindLocalDb();
        string instance = "AZIdentLab_" + Guid.NewGuid().ToString("N");
        string database = "AZIdentSynthetic_" + Guid.NewGuid().ToString("N");
        bool created = false;
        // These values are constructed here, never taken from a file, environment
        // variable, argument, or network endpoint. LocalDB is a per-user process.
        string master = ConnectionString(instance, "master");
        string synthetic = ConnectionString(instance, database);
        try
        {
            await LocalDb(localDb, "create", instance);
            created = true;
            await LocalDb(localDb, "start", instance);
            await using (var connection = new SqlConnection(master))
            {
                await connection.OpenAsync();
                using var cmd = connection.CreateCommand();
                cmd.CommandTimeout = 20;
                cmd.CommandText = "SELECT CONVERT(int,SERVERPROPERTY('IsLocalDB'))";
                Assert(Convert.ToInt32(await cmd.ExecuteScalarAsync(), CultureInfo.InvariantCulture) == 1, "only LocalDB may run these tests");
                cmd.CommandText = $"CREATE DATABASE [{database}]";
                await cmd.ExecuteNonQueryAsync();
                cmd.CommandText = $"ALTER DATABASE [{database}] SET COMPATIBILITY_LEVEL=110";
                await cmd.ExecuteNonQueryAsync();
            }
            await Execute(synthetic, Schema);
            await VerifyReaderRights(synthetic);
            await Baseline(synthetic);
            await IncompleteSources(synthetic);
            await Boundaries(synthetic);
            await DateDiscrepancy(synthetic);
            Console.WriteLine($"SQL_LAB={assertions}/{assertions} PASS; ENGINE=LOCALDB; COMPATIBILITY_LEVEL=110; REAL_DATA=NONE; PRODUCTION_TLS=NOT_EXERCISED; PRODUCTION_IDENTITY=NOT_EXERCISED");
            Console.WriteLine("SQL_LAB_RESOURCES_SHA256 hours=" + Hash(Resource("ident.hours.sql")) + "; primary=" + Hash(Resource("ident.primary.sql")));
        }
        finally
        {
            // Stop/delete only this run's unguessable, generated LocalDB instance.
            // Never enumerate or stop other instances or Windows services.
            if (created)
            {
                try
                {
                    await using var connection = new SqlConnection(master);
                    await connection.OpenAsync();
                    using var cmd = connection.CreateCommand();
                    cmd.CommandTimeout = 20;
                    cmd.CommandText = $"IF DB_ID(N'{database}') IS NOT NULL BEGIN ALTER DATABASE [{database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [{database}]; END";
                    await cmd.ExecuteNonQueryAsync();
                }
                finally
                {
                    try { await LocalDb(localDb, "stop", instance, "-k"); }
                    finally { await LocalDb(localDb, "delete", instance); }
                }
            }
        }
    }

    private static string FindLocalDb()
    {
        string root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Microsoft SQL Server");
        if (Directory.Exists(root))
        {
            foreach (string version in Directory.GetDirectories(root).OrderByDescending(Path.GetFileName, StringComparer.Ordinal))
            {
                string candidate = Path.Combine(version, "Tools", "Binn", "SqlLocalDB.exe");
                if (File.Exists(candidate)) return candidate;
            }
        }
        throw new InvalidOperationException("SQL_LAB_PREREQUISITE_MISSING: preinstalled SqlLocalDB.exe; installation is not permitted by this harness");
    }

    private static string ConnectionString(string instance, string database) => new SqlConnectionStringBuilder
    {
        DataSource = @"(localdb)\" + instance,
        InitialCatalog = database,
        IntegratedSecurity = true,
        // LocalDB uses a local named pipe. This deliberately separate test route
        // does not claim to exercise, replace, or relax production TLS PINNED.
        Encrypt = SqlConnectionEncryptOption.Optional,
        TrustServerCertificate = false,
        Pooling = false,
        ConnectTimeout = 15,
        ConnectRetryCount = 0,
        ApplicationName = "IDENT synthetic SQL laboratory"
    }.ConnectionString;

    private static async Task LocalDb(string executable, params string[] arguments)
    {
        var start = new ProcessStartInfo(executable) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (string arg in arguments) start.ArgumentList.Add(arg);
        using var process = Process.Start(start) ?? throw new InvalidOperationException("SQL_LAB_LOCALDB_START_FAILED");
        Task<string> stdout = process.StandardOutput.ReadToEndAsync();
        Task<string> stderr = process.StandardError.ReadToEndAsync();
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(40));
        try { await process.WaitForExitAsync(deadline.Token); }
        catch { try { process.Kill(entireProcessTree: true); } catch (InvalidOperationException) { } throw; }
        string output = await stdout;
        string error = await stderr;
        if (process.ExitCode != 0) throw new InvalidOperationException($"SQL_LAB_LOCALDB_COMMAND_FAILED ({arguments[0]}): {output} {error}");
    }

    private static async Task Execute(string connectionString, string sql)
    {
        await using var connection = new SqlConnection(connectionString);
        await connection.OpenAsync();
        using var cmd = connection.CreateCommand();
        cmd.CommandTimeout = 20;
        cmd.CommandText = sql;
        await cmd.ExecuteNonQueryAsync();
    }

    private static async Task VerifyReaderRights(string connectionString)
    {
        await using var connection = new SqlConnection(connectionString);
        await connection.OpenAsync();
        using var cmd = connection.CreateCommand();
        cmd.CommandTimeout = 20;
        cmd.CommandText = "EXECUTE AS USER=N'ident_lab_reader'; SELECT USER_NAME(), HAS_PERMS_BY_NAME(N'dbo.Receptions',N'OBJECT',N'SELECT'), HAS_PERMS_BY_NAME(N'dbo.Receptions',N'OBJECT',N'UPDATE'), HAS_PERMS_BY_NAME(DB_NAME(),N'DATABASE',N'ALTER'), compatibility_level FROM sys.databases WHERE name=DB_NAME()";
        using (var reader = await cmd.ExecuteReaderAsync())
        {
            Assert(await reader.ReadAsync(), "reader rights result exists");
            Assert(reader.GetString(0) == "ident_lab_reader" && reader.GetInt32(1) == 1 && reader.GetInt32(2) == 0 && reader.GetInt32(3) == 0, "synthetic query user has SELECT and no UPDATE/ALTER");
            Assert(reader.GetByte(4) == 110, "database compatibility110");
            Assert(!await reader.ReadAsync() && !await reader.NextResultAsync(), "single reader rights result");
        }
        cmd.CommandText = "UPDATE dbo.Receptions SET ID_Staffs=ID_Staffs WHERE 1=0";
        bool denied = false;
        try { await cmd.ExecuteNonQueryAsync(); }
        catch (SqlException ex) when (ex.Number == 229) { denied = true; }
        Assert(denied, "database rejects UPDATE even when it would affect no rows");
    }

    private static string Resource(string name)
    {
        using var stream = typeof(ResearchSql).Assembly.GetManifestResourceStream(name) ?? throw new InvalidOperationException("SQL_LAB_RESOURCE_MISSING: " + name);
        using var reader = new StreamReader(stream, new UTF8Encoding(false, true));
        return reader.ReadToEnd().Replace("\r\n", "\n").Replace("\r", "\n");
    }
    private static string Hash(string value) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value))).ToLowerInvariant();

    private static async Task<List<Dictionary<string, object>>> Query(string connectionString, bool hours, int? staff = null, DateTime? from = null, DateTime? to = null, DateTime? cutoff = null, DateTime? observed = null)
    {
        await using var connection = new SqlConnection(connectionString);
        await connection.OpenAsync();
        using var cmd = connection.CreateCommand();
        cmd.CommandTimeout = 20;
        cmd.CommandText = "EXECUTE AS USER=N'ident_lab_reader'";
        await cmd.ExecuteNonQueryAsync();
        cmd.CommandText = Resource(hours ? "ident.hours.sql" : "ident.primary.sql");
        cmd.Parameters.Add("@date_from", SqlDbType.DateTime).Value = from ?? From;
        cmd.Parameters.Add("@date_to", SqlDbType.DateTime).Value = to ?? To;
        cmd.Parameters.Add("@fact_end", SqlDbType.DateTime).Value = cutoff ?? Cutoff;
        cmd.Parameters.Add("@observed", SqlDbType.DateTime).Value = observed ?? Cutoff;
        if (!hours) cmd.Parameters.Add("@staff_id", SqlDbType.Int).Value = staff ?? throw new ArgumentException("staff required");
        using var reader = await cmd.ExecuteReaderAsync();
        string[] expected = hours ? Output.HoursColumns : Output.PrimaryColumns;
        Assert(reader.FieldCount == expected.Length && Enumerable.Range(0, expected.Length).All(i => reader.GetName(i) == expected[i]), "exact aggregate columns; no patient/reception identifiers returned");
        var rows = new List<Dictionary<string, object>>();
        while (await reader.ReadAsync())
        {
            var row = new Dictionary<string, object>(StringComparer.Ordinal);
            for (int i = 0; i < reader.FieldCount; i++)
            {
                Assert(!reader.IsDBNull(i), "aggregate field is not NULL: " + expected[i]);
                object value = reader.GetValue(i);
                // Match the existing transport representation before exercising
                // its unmodified fail-closed Output.Validate checks.
                row.Add(expected[i], value switch
                {
                    int n => (long)n,
                    long n => n,
                    decimal amount => amount.ToString("0.000000", CultureInfo.InvariantCulture),
                    _ => value
                });
            }
            rows.Add(row);
        }
        Assert(!await reader.NextResultAsync(), "exactly one aggregate result set");
        return rows;
    }

    private static void BlockedOutput(List<Dictionary<string, object>> rows, bool hours, string expected)
    {
        bool rejected = false;
        try { Output.Validate(rows, hours, hours ? null : 1); }
        catch (Blocked ex) when (ex.Message == expected) { rejected = true; }
        Assert(rejected, "existing output guard rejects " + expected);
    }

    private static async Task Baseline(string connection)
    {
        await Execute(connection, BaselineData);
        var hours = await Query(connection, true);
        Output.Validate(hours, true, null);
        Assert(hours.Count == 2, "two active synthetic staff aggregates; inactive staff not invented");
        var one = Staff(hours, 1);
        Assert((string)one["staff_name"] == "Synthetic One Test" && !(bool)one["archived"], "staff1 identity");
        Assert(N(one, "schedule_seconds") == 10800, "duplicate and overlapping schedule slots form a 3-hour union");
        Assert(N(one, "actual_seconds") == 10800, "overlapping completed visits plus separate dates form a 3-hour union");
        Assert(N(one, "booked_seconds") == 9000, "future bookings merge overlaps and clip at month end");
        Assert(N(one, "reserved_seconds") == 5400, "one 90-minute reserve");
        Assert(N(one, "completed_visits") == 4 && N(one, "booked_visits") == 3 && N(one, "appeared_incomplete") == 1, "completed/booked/appeared counts and cancellation exclusions");
        Assert(M(one, "revenue_total") == 400m && M(one, "revenue_linked_completed") == 345m && N(one, "service_rows") == 8, "net rendered amounts include refund and zero; linked and unlinked amounts remain distinct");
        var two = Staff(hours, 2);
        Assert((bool)two["archived"] && N(two, "actual_seconds") == 900 && N(two, "completed_visits") == 1 && M(two, "revenue_total") == 70m, "second archived specialist is isolated");
        var primary = await Query(connection, false, 1);
        Output.Validate(primary, false, 1);
        Assert(primary.All(r => N(r, "total_visits") == 4 && N(r, "total_unique_patients") == 3 && M(r, "revenue_total") == 400m && M(r, "revenue_linked_completed") == 345m), "classification controls reconcile to independently expected totals");
        CheckCategory(primary, "doctor_primary", 2, 2, 200m);
        CheckCategory(primary, "doctor_repeat", 2, 1, 145m);
        CheckCategory(primary, "clinic_primary", 1, 1, 0m);
        CheckCategory(primary, "clinic_repeat", 3, 2, 345m);
        var knownEmpty = await Query(connection, false, 3);
        Output.Validate(knownEmpty, false, 3);
        Assert(knownEmpty.All(r => N(r, "total_visits") == 0 && N(r, "visits") == 0 && M(r, "revenue_total") == 0m), "known staff with no activity returns four zero categories");
        var unknown = await Query(connection, false, 999);
        Assert(unknown.Count == 0, "unknown staff produces no invented identity");
        BlockedOutput(unknown, false, "STAFF_NOT_FOUND_OR_CATEGORY_MISSING");
        var empty = await Query(connection, true, from: D("2027-01-01T00:00:00"), to: D("2027-01-02T00:00:00"), cutoff: D("2027-01-02T00:00:00"), observed: D("2027-01-02T00:00:00"));
        Output.Validate(empty, true, null);
        Assert(empty.Count == 0, "empty period remains empty");
        Console.WriteLine("SQL_LAB_BASELINE=PASS; schedule_seconds=10800; actual_seconds=10800; booked_seconds=9000; rendered_total=400.000000; linked_completed=345.000000; visits=4; unique_patients=3");
    }

    private static void CheckCategory(List<Dictionary<string, object>> rows, string name, long visits, long patients, decimal amount)
    {
        var row = Category(rows, name);
        Assert(N(row, "visits") == visits && N(row, "patients") == patients && M(row, "revenue") == amount, name + " visits, unique patients, rendered amount");
    }

    private static async Task IncompleteSources(string connection)
    {
        await Execute(connection, "INSERT dbo.LabRenderedServices VALUES (1,101,'20261111',NULL)");
        var hours = await Query(connection, true);
        var primary = await Query(connection, false, 1);
        Assert(N(Staff(hours, 1), "null_amount_count") == 1 && primary.All(r => N(r, "null_amount_count") == 1), "NULL amount stays visible as incomplete source");
        BlockedOutput(hours, true, "INCOMPLETE_REVENUE_SOURCE");
        BlockedOutput(primary, false, "INCOMPLETE_CLASSIFICATION_OR_REVENUE");
        await Execute(connection, "DELETE dbo.LabRenderedServices WHERE AmountWithDiscount IS NULL; INSERT dbo.LabRenderedServices VALUES (1,999999,'20261111',12.00)");
        hours = await Query(connection, true);
        primary = await Query(connection, false, 1);
        Assert(N(Staff(hours, 1), "missing_order_count") == 1 && primary.All(r => N(r, "missing_order_count") == 1), "missing order stays visible as incomplete source");
        BlockedOutput(hours, true, "INCOMPLETE_REVENUE_SOURCE");
        BlockedOutput(primary, false, "INCOMPLETE_CLASSIFICATION_OR_REVENUE");
        await Execute(connection, "DELETE dbo.LabRenderedServices WHERE ID_Orders=999999; INSERT dbo.Receptions (ID,ID_Staffs,ID_Patients,PlanStart,PlanEnd,ReceptionStarted,ReceptionEnded) VALUES (999,1,NULL,'20261111 09:00','20261111 09:15','20261111 09:00','20261111 09:15')");
        primary = await Query(connection, false, 1);
        Assert(primary.All(r => N(r, "unclassified_visits") == 1), "NULL patient cannot disappear as a valid classification");
        BlockedOutput(primary, false, "INCOMPLETE_CLASSIFICATION_OR_REVENUE");
        Console.WriteLine("SQL_LAB_INCOMPLETE_SOURCE=PASS; null_amount=REJECTED; missing_order=REJECTED; unclassified_visit=REJECTED; synthetic_update=DENIED");
    }

    private static async Task Boundaries(string connection)
    {
        await Execute(connection, Reset + BoundaryData);
        var hours = await Query(connection, true);
        Output.Validate(hours, true, null);
        var row = Staff(hours, 1);
        Assert(N(row, "completed_visits") == 2 && N(row, "actual_seconds") == 5400, "date_from is inclusive and ReceptionEnded equal fact_end is completed");
        Assert(N(row, "booked_visits") == 1 && N(row, "booked_seconds") == 1800, "PlanStart equal fact_end is future booking; date_to is exclusive");
        Assert(N(row, "appeared_incomplete") == 1, "completion after fact_end stays incomplete at cutoff");
        Assert(M(row, "revenue_total") == 30m && M(row, "revenue_linked_completed") == 30m && N(row, "service_rows") == 2, "rendered-service date_from inclusive and fact_end exclusive");
        var primary = await Query(connection, false, 1, observed: D("2026-11-15T14:00:00"));
        Output.Validate(primary, false, 1);
        CheckCategory(primary, "doctor_primary", 2, 2, 30m);
        CheckCategory(primary, "clinic_primary", 2, 2, 30m);
        CheckCategory(primary, "doctor_repeat", 0, 0, 0m);
        CheckCategory(primary, "clinic_repeat", 0, 0, 0m);
        Assert(primary.All(r => N(r, "total_visits") == 2 && M(r, "revenue_total") == 30m), "observed clock later than cutoff does not admit future target visits");
        Console.WriteLine("SQL_LAB_BOUNDARIES=PASS; date_from=INCLUSIVE; service_fact_end=EXCLUSIVE; completion_fact_end=INCLUSIVE; date_to=EXCLUSIVE");
    }

    private static async Task DateDiscrepancy(string connection)
    {
        await Execute(connection, Reset + """
INSERT dbo.Receptions (ID,ID_Staffs,ID_Patients,PlanStart,PlanEnd,ReceptionStarted,ReceptionEnded)
VALUES (1,1,1001,'20261110 09:00','20261110 10:00','20261031 09:00','20261031 10:00');
INSERT dbo.Orders VALUES (101,1);
INSERT dbo.LabRenderedServices VALUES (1,101,'20261110 10:00',100.00);
""");
        var hours = await Query(connection, true);
        var primary = await Query(connection, false, 1);
        // Both current output guards accept this disagreement. The test records
        // existing behavior only: it is not an approval of either business rule.
        Output.Validate(hours, true, null);
        Output.Validate(primary, false, 1);
        var row = Staff(hours, 1);
        Assert(N(row, "completed_visits") == 0 && N(row, "actual_seconds") == 0 && M(row, "revenue_total") == 100m && M(row, "revenue_linked_completed") == 0m, "performance excludes completion before report start");
        Assert(primary.All(r => N(r, "total_visits") == 1 && N(r, "total_unique_patients") == 1 && M(r, "revenue_total") == 100m && M(r, "revenue_linked_completed") == 100m), "primary/repeat includes same earlier completion by November PlanStart");
        CheckCategory(primary, "doctor_primary", 1, 1, 100m);
        CheckCategory(primary, "clinic_primary", 1, 1, 100m);
        Console.WriteLine("KNOWN_DATE_DISCREPANCY=REPRODUCED; plan_start=2026-11-10; completion=2026-10-31; service_date=2026-11-10; performance_visits=0; primary_repeat_visits=1; performance_linked=0.000000; primary_repeat_linked=100.000000; BUSINESS_RULE_CHANGE=NONE; BUSINESS_DECISION=UNRESOLVED");
    }

    private const string Schema = """
CREATE TABLE dbo.Persons (ID int NOT NULL PRIMARY KEY, Surname nvarchar(50) NOT NULL, Name nvarchar(50) NOT NULL, Patronymic nvarchar(50) NOT NULL);
CREATE TABLE dbo.Staffs (ID_Persons int NOT NULL PRIMARY KEY, Archive bit NOT NULL);
CREATE TABLE dbo.Receptions (ID int NOT NULL PRIMARY KEY, ID_Staffs int NULL, ID_Patients int NULL, PlanStart datetime NULL, PlanEnd datetime NULL, ReceptionStarted datetime NULL, ReceptionEnded datetime NULL, PatientAppeared datetime NULL, ReceptionCanceled datetime NULL, ID_ReceptionCancelReasons int NULL);
CREATE TABLE dbo.Orders (ID int NOT NULL PRIMARY KEY, ID_Receptions int NULL);
CREATE TABLE dbo.LabRenderedServices (ID_Staffs int NULL, ID_Orders int NULL, DateTimeOrder datetime NULL, AmountWithDiscount decimal(18,2) NULL);
EXEC(N'CREATE VIEW dbo.RenderedServicesJournal AS SELECT ID_Staffs,ID_Orders,DateTimeOrder,AmountWithDiscount FROM dbo.LabRenderedServices');
CREATE TABLE dbo.Times (ID tinyint NOT NULL PRIMARY KEY, TimeStartValue time NOT NULL, TimeEndValue time NOT NULL);
CREATE TABLE dbo.CurrentTimeTable (ID_Staffs int NULL, ID_Times tinyint NULL, DateOfWork date NULL, IsWorkingTime bit NULL, IsHeaderCell bit NULL, ID_TimeReserves int NULL);
INSERT dbo.Persons VALUES (1,N'Synthetic',N'One',N'Test'),(2,N'Synthetic',N'Two',N'Test'),(3,N'Synthetic',N'Empty',N'Test');
INSERT dbo.Staffs VALUES (1,0),(2,1),(3,0);
CREATE USER ident_lab_reader WITHOUT LOGIN;
GRANT SELECT ON dbo.Persons TO ident_lab_reader;
GRANT SELECT ON dbo.Staffs TO ident_lab_reader;
GRANT SELECT ON dbo.Receptions TO ident_lab_reader;
GRANT SELECT ON dbo.Orders TO ident_lab_reader;
GRANT SELECT ON dbo.RenderedServicesJournal TO ident_lab_reader;
GRANT SELECT ON dbo.Times TO ident_lab_reader;
GRANT SELECT ON dbo.CurrentTimeTable TO ident_lab_reader;
""";

    private const string Reset = """
DELETE dbo.LabRenderedServices;
DELETE dbo.Orders;
DELETE dbo.Receptions;
DELETE dbo.CurrentTimeTable;
DELETE dbo.Times;

""";

    // Expected baseline, calculated directly from these synthetic facts:
    // hours: schedule 3, actual 3, booked 2.5, reserves 1.5;
    // staff1: 4 visits / 3 people; 400 rendered / 345 linked;
    // doctor primary/repeat amounts 200/145, clinic primary/repeat 0/345.
    private const string BaselineData = """
INSERT dbo.Times VALUES (1,'09:00','10:00'),(2,'09:30','11:00'),(3,'11:00','12:00'),(4,'07:00','08:00');
INSERT dbo.CurrentTimeTable VALUES
(1,1,'20261110',1,0,NULL),(1,1,'20261110',1,0,NULL),(1,2,'20261110',1,0,7),(1,3,'20261110',1,0,NULL),
(1,4,'20261110',1,1,NULL),(1,4,'20261110',0,0,NULL),(1,1,'20261201',1,0,NULL);
INSERT dbo.Receptions (ID,ID_Staffs,ID_Patients,PlanStart,PlanEnd,ReceptionStarted,ReceptionEnded,PatientAppeared,ReceptionCanceled,ID_ReceptionCancelReasons) VALUES
(901,1,101,'20261020 09:00','20261020 09:30','20261020 09:00','20261020 09:30',NULL,NULL,NULL),
(902,2,102,'20261021 09:00','20261021 09:30','20261021 09:00','20261021 09:30',NULL,NULL,NULL),
(1,1,101,'20261110 09:00','20261110 10:00','20261110 09:00','20261110 10:00',NULL,NULL,NULL),
(2,1,102,'20261110 09:30','20261110 11:00','20261110 09:30','20261110 11:00',NULL,NULL,NULL),
(3,1,101,'20261111 10:00','20261111 10:30','20261111 10:00','20261111 10:30',NULL,NULL,NULL),
(4,1,106,'20261120 09:00','20261120 10:00',NULL,NULL,NULL,NULL,NULL),
(5,1,107,'20261120 09:30','20261120 11:00',NULL,NULL,NULL,NULL,NULL),
(6,1,109,'20261112 09:00','20261112 09:30',NULL,NULL,'20261112 09:00',NULL,NULL),
(7,1,104,'20261110 12:00','20261110 13:00','20261110 12:00','20261110 13:00',NULL,'20261109',NULL),
(8,1,105,'20261110 14:00','20261110 15:00','20261110 14:00','20261110 15:00',NULL,NULL,1),
(9,1,108,'20261130 23:30','20261201 00:30',NULL,NULL,NULL,NULL,NULL),
(10,1,103,'20261114 09:00','20261114 09:30','20261114 09:00','20261114 09:30',NULL,NULL,NULL),
(201,2,201,'20261105 14:00','20261105 14:15','20261105 14:00','20261105 14:15',NULL,NULL,NULL);
INSERT dbo.Orders VALUES (101,1),(102,2),(103,3),(107,7),(110,10),(150,NULL),(201,201);
INSERT dbo.LabRenderedServices VALUES
(1,101,'20261110 10:00',100.00),(1,101,'20261111 10:00',-10.00),(1,102,'20261110 11:00',200.00),
(1,103,'20261111 10:30',50.00),(1,110,'20261114 09:30',0.00),(1,107,'20261110 13:00',30.00),
(1,150,'20261111 11:00',25.00),(1,101,'20261101 00:00',5.00),
(1,101,'20261031 23:59',50000.00),(1,101,'20261115 12:00',60000.00),(1,101,'20261201 00:00',70000.00),
(2,201,'20261105 14:15',70.00);
""";

    private const string BoundaryData = """
INSERT dbo.Receptions (ID,ID_Staffs,ID_Patients,PlanStart,PlanEnd,ReceptionStarted,ReceptionEnded,PatientAppeared) VALUES
(1,1,1001,'20261101 00:00','20261101 00:30','20261101 00:00','20261101 00:30',NULL),
(2,1,1002,'20261115 11:00','20261115 12:00','20261115 11:00','20261115 12:00',NULL),
(3,1,1003,'20261115 12:00','20261115 12:30',NULL,NULL,NULL),
(4,1,1004,'20261201 00:00','20261201 00:30',NULL,NULL,NULL),
(5,1,1005,'20261115 11:30','20261115 13:00','20261115 11:30','20261115 13:00','20261115 11:30');
INSERT dbo.Orders VALUES (101,1),(102,2);
INSERT dbo.LabRenderedServices VALUES
(1,101,'20261101 00:00',10.00),(1,102,'20261115 11:59:59',20.00),
(1,102,'20261115 12:00',30.00),(1,101,'20261031 23:59:59',40.00);
""";
}
