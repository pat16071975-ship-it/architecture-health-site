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


internal sealed record ResearchSettings(string Source,string ZoneName,string Host,int Port,string Database,string User,string PinHash,byte[] Binding)
{
    internal TimeZoneInfo Zone => Guard.Zone(ZoneName);
    internal static ResearchSettings Read(byte[] raw)
    {
        using var doc=Guard.Parse(raw,8192);
        var d=Guard.Object(doc.RootElement,"source_id","source_timezone","sql_host","sql_port","sql_database","sql_user","certificate_sha256");
        string T(string k)=>Guard.Text(d,k);
        Guard.Require(Regex.IsMatch(T("sql_host"),@"\A[A-Za-z0-9][A-Za-z0-9.\-]{0,252}\z") &&
            T("sql_database").Length is >0 and <=128 && T("sql_user").Length is >0 and <=128 &&
            !T("sql_database").Any(char.IsControl) && !T("sql_user").Any(char.IsControl) &&
            Guard.Hex(T("certificate_sha256")),"CONFIG_INVALID");
        Guard.Zone(T("source_timezone"));
        return new ResearchSettings(Guard.Uuid(T("source_id")),T("source_timezone"),T("sql_host"),
            (int)Guard.Int(d,"sql_port",1,65535),T("sql_database"),T("sql_user"),T("certificate_sha256"),SHA256.HashData(raw));
    }
}
internal sealed record ReportWindow(DateOnly From,DateOnly To,int? StaffId)
{
    internal static ReportWindow Parse(string from,string to,int? staffId)
    {
        var start=Guard.Date(from);var end=Guard.Date(to);
        Guard.Require(start.Year>=1900 && end.Year<=2100 && end.DayNumber-start.DayNumber is >=1 and <=31,"DATE_WINDOW_1_TO_31_DAYS");
        Guard.Require(staffId is null or >0,"STAFF_ID_INVALID");
        return new ReportWindow(start,end,staffId);
    }
}
