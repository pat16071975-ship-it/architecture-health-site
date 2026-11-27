using System.Globalization;
internal static class Output
{
    internal static readonly string[] HoursColumns={"staff_id","staff_name","archived","schedule_seconds","actual_seconds","booked_seconds","reserved_seconds","completed_visits","booked_visits","appeared_incomplete","revenue_total","revenue_linked_completed","service_rows","null_amount_count","missing_order_count"};
    internal static readonly string[] PrimaryColumns={"staff_id","staff_name","category","visits","patients","revenue","total_visits","total_unique_patients","revenue_total","revenue_linked_completed","unclassified_visits","service_rows","null_amount_count","missing_order_count"};
    internal static readonly string[] Categories={"clinic_primary","clinic_repeat","doctor_primary","doctor_repeat"};
    private static long N(Dictionary<string,object> r,string k)=>(long)r[k];
    private static decimal M(Dictionary<string,object> r,string k)=>decimal.Parse((string)r[k],CultureInfo.InvariantCulture);
    internal static void Validate(List<Dictionary<string,object>> rows,bool hours,int? staff)
    {
        if(hours){
            var ids=new HashSet<long>();
            foreach(var r in rows){
                Guard.Require(N(r,"staff_id")>0&&ids.Add(N(r,"staff_id")),"DUPLICATE_OR_UNKNOWN_STAFF");
                Guard.Require(N(r,"reserved_seconds")<=N(r,"schedule_seconds"),"RESERVE_EXCEEDS_SCHEDULE");
                Guard.Require(N(r,"null_amount_count")==0&&N(r,"missing_order_count")==0,"INCOMPLETE_REVENUE_SOURCE");
            }
            return;
        }
        Guard.Require(rows.Count==4,"STAFF_NOT_FOUND_OR_CATEGORY_MISSING");
        var map=new Dictionary<string,Dictionary<string,object>>(StringComparer.Ordinal);
        foreach(var r in rows){
            Guard.Require(N(r,"staff_id")==staff&&map.TryAdd((string)r["category"],r),"CATEGORY_DUPLICATE_OR_STAFF_MISMATCH");
            Guard.Require(N(r,"patients")<=N(r,"visits")&&N(r,"unclassified_visits")==0&&N(r,"null_amount_count")==0&&N(r,"missing_order_count")==0,"INCOMPLETE_CLASSIFICATION_OR_REVENUE");
        }
        var f=rows[0];
        foreach(var r in rows)
            foreach(string k in new[]{"staff_name","total_visits","total_unique_patients","revenue_total","revenue_linked_completed","unclassified_visits","service_rows","null_amount_count","missing_order_count"})
                Guard.Require(Equals(f[k],r[k]),"CONTROL_TOTAL_MISMATCH");
        foreach(var prefix in new[]{"clinic","doctor"}){
            var a=map[prefix+"_primary"];var b=map[prefix+"_repeat"];
            Guard.Require(N(a,"patients")==N(a,"visits")&&N(a,"visits")+N(b,"visits")==N(f,"total_visits")&&
                M(a,"revenue")+M(b,"revenue")==M(f,"revenue_linked_completed"),"CLASSIFICATION_RECONCILIATION");
        }
    }
}
