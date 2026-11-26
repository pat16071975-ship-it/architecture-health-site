using System.Globalization;
using System.Numerics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

internal sealed class Blocked(string code) : Exception(code) { }
internal sealed class Retry(string code) : Exception(code) { }
internal static class Guard
{
    internal const string Query = "277cc67cb4c0369f7f3461ae5d8c50ac0443c3342cb44a81625cf701c52e443f";
    internal const string Dataset = "payments_daily_v1";
    internal const int MaxPayload = 512 * 1024;
    internal const long MaxSequence = 9007199254740991;
    internal static readonly CultureInfo Inv = CultureInfo.InvariantCulture;
    internal static void Require(bool ok, string reason) { if (!ok) throw new Blocked(reason); }
    internal static string Hash(byte[] raw) => Convert.ToHexString(SHA256.HashData(raw)).ToLowerInvariant();
    internal static bool Hex(string s) => Regex.IsMatch(s, "\\A[0-9a-f]{64}\\z", RegexOptions.CultureInvariant);
    internal static string Uuid(string s)
    {
        Require(Guid.TryParseExact(s, "D", out var id) && id != Guid.Empty && id.ToString("D") == s, "INVALID_UUID");
        return s;
    }
    internal static DateOnly Date(string s)
    {
        Require(DateOnly.TryParseExact(s, "yyyy-MM-dd", Inv, DateTimeStyles.None, out var d) && d.ToString("yyyy-MM-dd", Inv) == s, "INVALID_DATE");
        return d;
    }
    internal static string Day(DateOnly d) => d.ToString("yyyy-MM-dd", Inv);
    internal static DateTimeOffset Utc(string s)
    {
        Require(Regex.IsMatch(s, "\\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,6})?Z\\z"), "INVALID_UTC");
        Require(DateTimeOffset.TryParse(s, Inv, DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var dt), "INVALID_UTC");
        return dt;
    }
    internal static string UtcText(DateTimeOffset dt) => dt.UtcDateTime.ToString("yyyy-MM-ddTHH:mm:ss.ffffff'Z'", Inv);
    internal static Dictionary<string,JsonElement> Object(JsonElement value, params string[] keys)
    {
        Require(value.ValueKind == JsonValueKind.Object, "JSON_OBJECT");
        var result = new Dictionary<string,JsonElement>(StringComparer.Ordinal);
        foreach (var p in value.EnumerateObject()) Require(result.TryAdd(p.Name, p.Value), "DUPLICATE_JSON_KEY");
        Require(result.Count == keys.Length && keys.All(result.ContainsKey), "JSON_FIELDS");
        return result;
    }
    internal static JsonDocument Parse(byte[] raw, int limit = MaxPayload)
    {
        Require(raw.Length > 0 && raw.Length <= limit, "JSON_SIZE");
        try { return JsonDocument.Parse(raw, new JsonDocumentOptions { MaxDepth=12 }); }
        catch (JsonException) { throw new Blocked("INVALID_JSON"); }
    }
    internal static string Text(Dictionary<string,JsonElement> obj, string key)
    {
        Require(obj[key].ValueKind == JsonValueKind.String, "JSON_STRING_REQUIRED");
        return obj[key].GetString()!;
    }
    internal static long Int(JsonElement value, long min=0, long max=MaxSequence)
    {
        Require(value.ValueKind == JsonValueKind.Number && Regex.IsMatch(value.GetRawText(),"\\A(?:0|[1-9][0-9]*)\\z") && value.TryGetInt64(out _), "INVALID_INTEGER");
        long n=value.GetInt64();
        Require(n>=min && n<=max, "INTEGER_RANGE");
        return n;
    }
    internal static long Int(Dictionary<string,JsonElement> obj, string key, long min=0, long max=MaxSequence) => Int(obj[key], min, max);
    internal static BigInteger Money(string s, int scale)
    {
        Require(s.Length<=80 && Regex.IsMatch(s,"\\A-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?\\z"),"DECIMAL_STRING");
        var parts=s.TrimStart('-').Split('.');
        string frac=parts.Length==2?parts[1]:"";
        Require(frac.Length<=scale && parts[0].TrimStart('0').Length<=38-scale,"DECIMAL_PRECISION");
        var n=BigInteger.Parse(parts[0]+frac.PadRight(scale,'0'), NumberStyles.None, Inv);
        return s.StartsWith('-')?-n:n;
    }
    internal static TimeZoneInfo Zone(string iana)
    {
        Require(iana.Length is >0 and <=100 && TimeZoneInfo.TryConvertIanaIdToWindowsId(iana, out _),"IANA_ZONE_REQUIRED");
        if (!TimeZoneInfo.TryConvertIanaIdToWindowsId(iana,out var id)) throw new Blocked("IANA_ZONE_REQUIRED");
        return TimeZoneInfo.FindSystemTimeZoneById(id);
    }
    internal static DateOnly LocalDay(DateTimeOffset now, TimeZoneInfo zone) => DateOnly.FromDateTime(TimeZoneInfo.ConvertTime(now,zone).DateTime);
}

internal sealed record Settings(string Organization, string Source, string Stream, string ZoneName, string Host, int Port,
    string Database, string User, string PinHash, Uri Origin, int Interval, int Lookback, long MaxBytes, long Reserve,
    string TokenHash, string SeedDigest, byte[] Binding)
{
    internal TimeZoneInfo Zone => Guard.Zone(ZoneName);
    internal static Settings Read(byte[] raw)
    {
        using var doc=Guard.Parse(raw,8192);
        var d=Guard.Object(doc.RootElement,"organization_id","source_id","stream_id","source_timezone","sql_host","sql_port","sql_database","sql_user","certificate_sha256","https_origin","interval_minutes","lookback_days","max_history_bytes","min_free_bytes","token_sha256","initial_batch_sha256");
        string T(string k)=>Guard.Text(d,k);
        string org=Guard.Uuid(T("organization_id")),source=Guard.Uuid(T("source_id")),stream=Guard.Uuid(T("stream_id"));
        Guard.Zone(T("source_timezone"));
        Guard.Require(Regex.IsMatch(T("sql_host"),"\\A[A-Za-z0-9][A-Za-z0-9.\\-]{0,252}\\z") && T("sql_database").Length is >0 and <=128 && T("sql_user").Length is >0 and <=128,"SQL_CONFIG");
        Guard.Require(Guard.Hex(T("certificate_sha256")) && Guard.Hex(T("token_sha256")) && Guard.Hex(T("initial_batch_sha256")),"HASH_REQUIRED");
        Guard.Require(Uri.TryCreate(T("https_origin"),UriKind.Absolute,out var origin) && origin.Scheme=="https" && origin.UserInfo=="" && origin.Query=="" && origin.Fragment=="" && origin.AbsolutePath=="/" && origin.Port==443 && origin.HostNameType==UriHostNameType.Dns && origin.IdnHost.Contains('.') && T("https_origin")==origin.GetLeftPart(UriPartial.Authority),"HTTPS_ORIGIN_REQUIRED");
        return new Settings(org,source,stream,T("source_timezone"),T("sql_host"),(int)Guard.Int(d,"sql_port",1,65535),
            T("sql_database"),T("sql_user"),T("certificate_sha256"),origin!, (int)Guard.Int(d,"interval_minutes",15,1440),
            (int)Guard.Int(d,"lookback_days",1,31), Guard.Int(d,"max_history_bytes",16L*1024*1024,8L*1024*1024*1024),
            Guard.Int(d,"min_free_bytes",16L*1024*1024,8L*1024*1024*1024),T("token_sha256"),T("initial_batch_sha256"),SHA256.HashData(raw));
    }
}

internal sealed record Batch(string Id,long Sequence,DateOnly From,DateOnly End,DateTimeOffset Observed,int Precision,int Scale,string Digest)
{
    internal static Batch Read(byte[] raw, Settings cfg, DateTimeOffset now)
    {
        using var doc=Guard.Parse(raw);
        var d=Guard.Object(doc.RootElement,"contract_version","dataset","scope","source_id","stream_id","batch_id","sequence","source_timezone","date_from","date_to_exclusive","query_started_at","observed_at","coverage","query_sha256","schema","rows");
        string T(string k)=>Guard.Text(d,k);
        Guard.Require(Guard.Int(d,"contract_version",1,1)==1 && T("dataset")==Guard.Dataset && T("scope")=="whole_source_database" && T("coverage")=="full_query_window_as_observed" && T("query_sha256")==Guard.Query,"CONTRACT");
        Guard.Require(T("source_id")==cfg.Source && T("stream_id")==cfg.Stream && T("source_timezone")==cfg.ZoneName,"SOURCE_BINDING");
        var start=Guard.Date(T("date_from")); var end=Guard.Date(T("date_to_exclusive")); int days=end.DayNumber-start.DayNumber;
        var started=Guard.Utc(T("query_started_at")); var observed=Guard.Utc(T("observed_at"));
        Guard.Require(days is >=1 and <=31 && started<=observed && observed<=now.AddMinutes(5) && end.DayNumber<=Guard.LocalDay(observed,cfg.Zone).DayNumber+1,"OBSERVATION_WINDOW");
        var schema=Guard.Object(d["schema"],"payment_precision","payment_scale");
        int precision=(int)Guard.Int(schema,"payment_precision",1,38), scale=(int)Guard.Int(schema,"payment_scale",0,precision);
        Guard.Require(d["rows"].ValueKind==JsonValueKind.Array && d["rows"].GetArrayLength()==days,"INCOMPLETE_COVERAGE");
        int i=0;
        foreach (var row in d["rows"].EnumerateArray())
        {
            var r=Guard.Object(row,"date","records_count","paying_patients","positive_amount","negative_amount","net_amount");
            Guard.Require(Guard.Date(Guard.Text(r,"date"))==start.AddDays(i++),"UNORDERED_DAY");
            long count=Guard.Int(r,"records_count"),patients=Guard.Int(r,"paying_patients",0,count);
            var pos=Guard.Money(Guard.Text(r,"positive_amount"),scale);
            var neg=Guard.Money(Guard.Text(r,"negative_amount"),scale);
            var net=Guard.Money(Guard.Text(r,"net_amount"),scale);
            Guard.Require(pos>=0 && neg<=0 && pos+neg==net && (patients>0)==(pos>0) && (count>0 || (pos==0 && neg==0 && net==0 && patients==0)),"MONEY_OR_PATIENT_MISMATCH");
        }
        return new Batch(Guard.Uuid(T("batch_id")),Guard.Int(d,"sequence",1),start,end,observed,precision,scale,Guard.Hash(raw));
    }

    internal static byte[] Create(Settings cfg,long seq,DateOnly start,DateOnly end,SqlReader.Result result)
    {
        var days=result.Rows.ToDictionary(r=>r.date,StringComparer.Ordinal);
        var rows=new List<SqlReader.Row>();
        for (var day=start;day<end;day=day.AddDays(1))
        {
            string date=Guard.Day(day);
            rows.Add(days.Remove(date,out var row)?row:new SqlReader.Row(date,0,0,"0","0","0"));
        }
        Guard.Require(days.Count==0,"EXTRA_SQL_DAY");
        return JsonSerializer.SerializeToUtf8Bytes(new {
            contract_version=1,dataset=Guard.Dataset,scope="whole_source_database",source_id=cfg.Source,stream_id=cfg.Stream,
            batch_id=Guid.NewGuid().ToString("D"),sequence=seq,source_timezone=cfg.ZoneName,date_from=Guard.Day(start),date_to_exclusive=Guard.Day(end),
            query_started_at=result.Started,observed_at=result.Finished,coverage="full_query_window_as_observed",query_sha256=Guard.Query,
            schema=new {payment_precision=result.Precision,payment_scale=result.Scale},rows
        });
    }

    internal void VerifyAck(byte[] raw,Settings cfg, DateTimeOffset now)
    {
        using var doc=Guard.Parse(raw,8192);
        var d=Guard.Object(doc.RootElement,"contract_version","dataset","source_id","stream_id","batch_id","sequence","payload_sha256","accepted_at","status");
        string T(string k)=>Guard.Text(d,k);
        Guard.Require(Guard.Int(d,"contract_version",1,1)==1 && T("dataset")==Guard.Dataset && T("source_id")==cfg.Source && T("stream_id")==cfg.Stream && T("batch_id")==Id && Guard.Int(d,"sequence",1)==Sequence && T("payload_sha256")==Digest && (T("status")=="accepted" || T("status")=="already_accepted"),"ACK_MISMATCH");
        Guard.Require(Guard.Utc(T("accepted_at"))<=now.AddMinutes(5),"ACK_TIME");
    }
}
