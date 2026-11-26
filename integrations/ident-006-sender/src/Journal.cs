using System.Security.Cryptography;
using System.Text.RegularExpressions;

internal sealed class Journal
{
    private readonly Settings cfg;
    private readonly string path;
    private readonly List<Batch> batches=new();
    private long acked;
    private long occupied;
    private DateOnly end;
    private readonly HashSet<string> ids=new(StringComparer.Ordinal);

    internal Journal(Settings settings,string directory,DateTimeOffset now)
    {
        cfg=settings;path=directory;
        PrivateStore.AssertAcl(new DirectoryInfo(path),true);
        var packets=new SortedDictionary<long,string>();
        var acks=new SortedDictionary<long,string>();
        foreach(string file in Directory.EnumerateFileSystemEntries(path))
        {
            var info=new FileInfo(file);PrivateStore.AssertAcl(info,true);
            occupied=checked(occupied+info.Length);
            string name=info.Name;
            if(Regex.IsMatch(name,"\\A\\.tmp-[0-9a-f]{32}\\z"))continue; // encrypted, unpublished, never sent
            var m=Regex.Match(name,"\\A(packet|ack)-([0-9]{16})\\.bin\\z");
            Guard.Require(m.Success&&long.TryParse(m.Groups[2].Value,out _),"UNKNOWN_JOURNAL_FILE");
            long seq=long.Parse(m.Groups[2].Value,Guard.Inv);
            Guard.Require(seq>0&&seq<=Guard.MaxSequence,"JOURNAL_SEQUENCE");
            Guard.Require((m.Groups[1].Value=="packet"?packets:acks).TryAdd(seq,file),"JOURNAL_DUPLICATE");
        }
        Guard.Require(packets.Count>0,"INITIAL_PACKET_MISSING");
        foreach(var (seq,file) in packets)
        {
            Guard.Require(seq==LastSequence+1,"JOURNAL_GAP");
            byte[] raw=ReadCipher(file,"packet",seq);
            try{AcceptMetadata(Batch.Read(raw,cfg,now));}
            finally{CryptographicOperations.ZeroMemory(raw);}
        }
        foreach(var (seq,file) in acks)
        {
            Guard.Require(seq==acked+1&&seq<=LastSequence,"ACK_JOURNAL_GAP");
            byte[] raw=ReadCipher(file,"ack",seq);
            try{batches[checked((int)seq-1)].VerifyAck(raw,cfg,now);acked=seq;}
            finally{CryptographicOperations.ZeroMemory(raw);}
        }
        Guard.Require(LastSequence-acked<=1,"QUEUE_INVARIANT");
    }
    internal long LastSequence=>batches.Count;
    internal long Acked=>acked;
    internal bool HasPending=>LastSequence!=acked;
    internal Batch Last=>batches[^1];
    internal DateOnly Start=>batches[0].From;
    internal DateOnly CoveredEnd=>end;
    private static string Filename(string kind,long seq)=>kind+"-"+seq.ToString("D16",Guard.Inv)+".bin";
    private string FilePath(string kind,long seq)=>Path.Combine(path,Filename(kind,seq));
    private static string Purpose(string kind,long seq)=>Filename(kind,seq);
    private byte[] ReadCipher(string file,string kind,long seq)=>Dpapi.Transform(PrivateStore.Read(file,Dpapi.PrivateStoreLimit,true),cfg.Binding,Purpose(kind,seq),false);

    private void CheckMetadata(Batch b)
    {
        Guard.Require(b.Sequence==LastSequence+1 && !ids.Contains(b.Id),"BATCH_SEQUENCE_CONFLICT");
        if(LastSequence==0)Guard.Require(b.Sequence==1&&b.Digest==cfg.SeedDigest,"SEED_DIGEST");
        else Guard.Require(b.From>=Start && b.From<=end && b.End>=end && b.Observed>=Last.Observed &&
                           b.Precision==Last.Precision && b.Scale==Last.Scale,"COVERAGE_SCHEMA_OR_CLOCK_CONFLICT");
    }
    private void AcceptMetadata(Batch b)
    {
        CheckMetadata(b);ids.Add(b.Id);batches.Add(b);end=b.End;
    }

    internal static void Seed(Settings cfg,string directory,byte[] raw,DateTimeOffset now)
    {
        PrivateStore.AssertAcl(new DirectoryInfo(directory),true);
        Guard.Require(!Directory.EnumerateFileSystemEntries(directory).Any(),"SEED_REQUIRES_EMPTY_JOURNAL");
        var batch=Batch.Read(raw,cfg,now);
        Guard.Require(batch.Sequence==1&&batch.Digest==cfg.SeedDigest,"SEED_DIGEST");
        var cipher=Dpapi.Transform(raw,cfg.Binding,Purpose("packet",1),true);
        try{PrivateStore.Publish(Path.Combine(directory,Filename("packet",1)),cipher,adminOwner:true);}
        finally{CryptographicOperations.ZeroMemory(cipher);}
    }
    internal byte[] PendingBytes(DateTimeOffset now)
    {
        Guard.Require(HasPending,"NO_PENDING_PACKET");
        byte[] raw=ReadCipher(FilePath("packet",LastSequence),"packet",LastSequence);
        try
        {
            Guard.Require(Batch.Read(raw,cfg,now)==Last,"PENDING_CHANGED");
            return raw;
        }
        catch{CryptographicOperations.ZeroMemory(raw);throw;}
    }
    internal void EnsureCapacity()
    {
        // Keep all immutable packets and receipts in this first release.
        // Reserve space for one maximum packet plus its receipt and FS overhead.
        long reserveForPacket=2L*Dpapi.PrivateStoreLimit+65536;
        var drive=new DriveInfo(Path.GetPathRoot(path)!);
        if(occupied+reserveForPacket>cfg.MaxBytes || drive.AvailableFreeSpace<cfg.Reserve+reserveForPacket)
            throw new Retry("LOCAL_QUEUE_CAPACITY");
    }
    internal void Append(byte[] raw,DateTimeOffset now)
    {
        Guard.Require(!HasPending,"PENDING_MUST_ACK_FIRST");
        EnsureCapacity();
        var batch=Batch.Read(raw,cfg,now);CheckMetadata(batch);
        var cipher=Dpapi.Transform(raw,cfg.Binding,Purpose("packet",batch.Sequence),true);
        try
        {
            PrivateStore.Publish(FilePath("packet",batch.Sequence),cipher);
            occupied=checked(occupied+cipher.Length);AcceptMetadata(batch);
        }
        finally{CryptographicOperations.ZeroMemory(cipher);}
    }
    internal void Ack(byte[] raw,DateTimeOffset now)
    {
        Guard.Require(HasPending,"NO_PENDING_PACKET");
        Last.VerifyAck(raw,cfg,now);
        var cipher=Dpapi.Transform(raw,cfg.Binding,Purpose("ack",LastSequence),true);
        try
        {
            PrivateStore.Publish(FilePath("ack",LastSequence),cipher);
            occupied=checked(occupied+cipher.Length);acked=LastSequence;
        }
        finally{CryptographicOperations.ZeroMemory(cipher);}
    }
    internal void VerifyRemote(long remote)
    {
        // Strict single producer and prefix. A restored/truncated remote store
        // requires operator reconciliation; do not allocate a new sequence.
        Guard.Require(remote>=acked&&remote<=LastSequence,"REMOTE_HISTORY_CONFLICT");
    }

    internal (DateOnly From,DateOnly End)? Plan(DateTimeOffset now)
    {
        Guard.Require(!HasPending,"PENDING_MUST_ACK_FIRST");
        Guard.Require(now.AddMinutes(5)>=Last.Observed,"CLOCK_REGRESSION");
        var todayEnd=Guard.LocalDay(now,cfg.Zone).AddDays(1);
        Guard.Require(todayEnd>=CoveredEnd,"LOCAL_DATE_REGRESSION");
        if(todayEnd==CoveredEnd && now-Last.Observed<TimeSpan.FromMinutes(cfg.Interval))return null;
        // If a gap is longer than 31 days, catch up in contiguous chunks.
        // Re-read the previous observed day at chunk boundaries; re-read the
        // configured rolling window after catch-up. Older edits need backfill.
        int startNum=Math.Max(Start.DayNumber,Math.Min(CoveredEnd.DayNumber-1,todayEnd.DayNumber-cfg.Lookback));
        var from=DateOnly.FromDayNumber(startNum);
        return (from,DateOnly.FromDayNumber(Math.Min(todayEnd.DayNumber,from.DayNumber+31)));
    }
}
