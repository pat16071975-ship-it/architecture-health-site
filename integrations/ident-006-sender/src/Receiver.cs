using System.Net;
using System.Net.Http.Headers;
using System.Security.Authentication;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

internal interface IReceiver
{
    Task<long> Status(CancellationToken ct);
    Task<byte[]> Send(byte[] payload,CancellationToken ct);
}

internal sealed class Receiver : IReceiver, IDisposable
{
    private readonly Settings cfg;
    private readonly HttpClient client;
    private const string Prefix="/api/ident-bridge/v1/";
    internal Receiver(Settings settings)
    {
        cfg=settings;
        var handler=new SocketsHttpHandler {
            UseProxy=false, AllowAutoRedirect=false, UseCookies=false,
            AutomaticDecompression=DecompressionMethods.None,
            ConnectTimeout=TimeSpan.FromSeconds(10),
            MaxResponseHeadersLength=16,
            MaxConnectionsPerServer=1,
            PooledConnectionLifetime=TimeSpan.FromMinutes(5)
        };
        // Default OS certificate chain and hostname verification. No callback,
        // insecure fallback, proxy, redirect, alternate host or HTTP fallback.
        handler.SslOptions.EnabledSslProtocols=SslProtocols.Tls12|SslProtocols.Tls13;
        client=new HttpClient(handler){Timeout=Timeout.InfiniteTimeSpan};
    }
    private async Task<byte[]> Request(HttpMethod method,string route,byte[]? data,CancellationToken ct)
    {
        using var timeout=CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromSeconds(60));
        byte[] token=Dpapi.ReadToken(cfg);
        try
        {
            using var req=new HttpRequestMessage(method,new Uri(cfg.Origin,Prefix+route)){
                Version=HttpVersion.Version11,VersionPolicy=HttpVersionPolicy.RequestVersionExact
            };
            req.Headers.Authorization=new AuthenticationHeaderValue("Bearer",Encoding.ASCII.GetString(token));
            req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
            if(data!=null)
            {
                req.Content=new ByteArrayContent(data);
                req.Content.Headers.ContentType=new MediaTypeHeaderValue("application/json");
                req.Content.Headers.ContentLength=data.Length;
            }
            using var response=await client.SendAsync(req,HttpCompletionOption.ResponseHeadersRead,timeout.Token).ConfigureAwait(false);
            int status=(int)response.StatusCode;
            if(status==429 || status>=500)throw new Retry("RECEIVER_TEMPORARY_FAILURE");
            if(status!=200)throw new Blocked(status switch {401=>"RECEIVER_UNAUTHORIZED",403=>"RECEIVER_FORBIDDEN",409=>"RECEIVER_CONFLICT",422=>"RECEIVER_REJECTED",_=>"RECEIVER_HTTP_REJECTED"});
            Guard.Require(response.Content.Headers.ContentType?.MediaType=="application/json" && response.Content.Headers.ContentEncoding.Count==0 &&
                response.Content.Headers.ContentLength is null or <=8192,"RESPONSE_FRAMING");
            using var stream=await response.Content.ReadAsStreamAsync(timeout.Token).ConfigureAwait(false);
            byte[] buffer=new byte[8193];int length=0;
            try
            {
                while(length<buffer.Length)
                {
                    int read=await stream.ReadAsync(buffer.AsMemory(length),timeout.Token).ConfigureAwait(false);
                    if(read==0)break;length+=read;
                }
                Guard.Require(length is >0 and <=8192,"RESPONSE_SIZE");
                return buffer.AsSpan(0,length).ToArray();
            }
            finally{CryptographicOperations.ZeroMemory(buffer);}
        }
        catch(OperationCanceledException) when(!ct.IsCancellationRequested){throw new Retry("RECEIVER_TIMEOUT");}
        catch(HttpRequestException){throw new Retry("RECEIVER_NETWORK_OR_TLS");}
        catch(IOException){throw new Retry("RECEIVER_NETWORK_OR_TLS");}
        finally{CryptographicOperations.ZeroMemory(token);}
    }
    internal static long ParseStatus(byte[] raw,Settings cfg,DateTimeOffset now)
    {
        using var doc=Guard.Parse(raw,8192);
        var d=Guard.Object(doc.RootElement,"status","dataset","source_id","stream_id","accepted_batches","max_sequence","last_accepted_at","first_observed_day","last_observed_day","mode");
        string T(string k)=>Guard.Text(d,k);
        Guard.Require(T("status")=="ready"&&T("dataset")==Guard.Dataset&&T("source_id")==cfg.Source&&T("stream_id")==cfg.Stream&&T("mode")=="shadow_only","RECEIVER_BINDING");
        long count=Guard.Int(d,"accepted_batches");
        if(count==0)
        {
            foreach(string key in new[]{"max_sequence","last_accepted_at","first_observed_day","last_observed_day"})
                Guard.Require(d[key].ValueKind==JsonValueKind.Null,"REMOTE_EMPTY_HISTORY");
        }
        else
        {
            Guard.Require(Guard.Int(d,"max_sequence",1)==count,"REMOTE_SEQUENCE_GAP");
            Guard.Require(Guard.Utc(T("last_accepted_at"))<=now.AddMinutes(5)&&Guard.Date(T("first_observed_day"))<=Guard.Date(T("last_observed_day")),"REMOTE_STATUS_TIME");
        }
        return count;
    }
    public async Task<long> Status(CancellationToken ct)
    {
        byte[] raw=await Request(HttpMethod.Get,"status",null,ct).ConfigureAwait(false);
        try{return ParseStatus(raw,cfg,DateTimeOffset.UtcNow);}
        finally{CryptographicOperations.ZeroMemory(raw);}
    }
    public Task<byte[]> Send(byte[] raw,CancellationToken ct)=>Request(HttpMethod.Post,"batches",raw,ct);
    public void Dispose()=>client.Dispose();
}

internal sealed class Cycle
{
    private readonly Settings cfg;
    private readonly Journal journal;
    private readonly IReceiver receiver;
    private readonly Func<DateOnly,DateOnly,CancellationToken,byte[]> collect;
    private readonly Func<DateTimeOffset> clock;
    internal Cycle(Settings settings,Journal store,IReceiver remote,Func<DateOnly,DateOnly,CancellationToken,byte[]> collector,Func<DateTimeOffset> now)
    {cfg=settings;journal=store;receiver=remote;collect=collector;clock=now;}
    internal async Task<string> Tick(CancellationToken ct)
    {
        ct.ThrowIfCancellationRequested();
        if(journal.HasPending)
        {
            // Even if the server committed but the response was lost, resend
            // exactly the persisted bytes, never a reconstructed packet.
            journal.VerifyRemote(await receiver.Status(ct).ConfigureAwait(false));
            byte[] pending=journal.PendingBytes(clock());
            try
            {
                byte[] ack=await receiver.Send(pending,ct).ConfigureAwait(false);
                try { journal.Ack(ack,clock()); }
                finally { CryptographicOperations.ZeroMemory(ack); }
            }
            finally { CryptographicOperations.ZeroMemory(pending); }
            return "ACK_PERSISTED";
        }
        var plan=journal.Plan(clock());
        if(plan is null)return "IDLE";
        // Allow one buffered collection during a temporary network outage.
        // A permanent authorization or history conflict must block collection.
        try{journal.VerifyRemote(await receiver.Status(ct).ConfigureAwait(false));}
        catch(Retry){ct.ThrowIfCancellationRequested();}
        journal.EnsureCapacity();
        byte[] raw=collect(plan.Value.From,plan.Value.End,ct);
        try{journal.Append(raw,clock());}
        finally{CryptographicOperations.ZeroMemory(raw);}
        return "PACKET_PERSISTED";
    }
}
