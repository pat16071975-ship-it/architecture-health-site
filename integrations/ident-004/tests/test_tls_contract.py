"""Source-level TLS regression checks, not a TLS handshake or Windows runtime test."""
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PS = (ROOT / "collector.ps1").read_text(encoding="utf-8")
WRAPPER = (ROOT / "pinned_sql.ps1").read_text(encoding="utf-8")
CS = (ROOT / "sql-client/Program.cs").read_text(encoding="utf-8")
PROJECT = ET.parse(ROOT / "sql-client/IdentSqlReader.csproj").getroot()


def test_standard_is_default_no_implicit_pin_or_downgrade():
    assert "[ValidateSet('Standard','PinnedCertificate')][string]$TlsMode = 'Standard'" in PS
    assert "if ($TlsMode -eq 'PinnedCertificate')" in PS
    assert "TLS_PIN_MODE_REQUIRED" in PS
    assert PS.count("$conn.Open()") == 1
    assert "$builder.Encrypt = $true" in PS
    assert "$builder.TrustServerCertificate = $false" in PS
    assert "Encrypt = SqlConnectionEncryptOption.Mandatory" in CS
    assert "TrustServerCertificate = false" in CS
    assert "ServerCertificate = cfg.CertificatePath" in CS
    assert "ConnectRetryCount = 0" in CS and "Pooling = false" in CS
    assert CS.count("conn.Open();") == 1
    for code in (PS, WRAPPER, CS):
        assert not re.search(r"TrustServerCertificate\s*=\s*(?:\$true|true)", code, re.I)
        assert not re.search(r"Encrypt\s*=\s*(?:\$false|false|SqlConnectionEncryptOption.Optional)", code, re.I)


def test_explicit_driver_and_certificate_digests_before_execution():
    assert WRAPPER.index("TLS_PIN_DIGEST_REQUIRED") < WRAPPER.index("$process.Start()")
    assert WRAPPER.index("Assert-PinnedInputFile $exe") < WRAPPER.index("ComputeHash($lock)")
    assert WRAPPER.index("ComputeHash($lock)") < WRAPPER.index("TLS_PIN_WORKER_DIGEST_MISMATCH") < WRAPPER.index("$process.Start()")
    assert "FileShare]::Read" in WRAPPER
    assert "GetAccessControl()" in WRAPPER and "ReparsePoint" in WRAPPER
    assert "Assert-PrivateOutbox (Join-Path $Outbox 'connection')" in WRAPPER
    assert "Assert-PrivateOutbox (Join-Path $dir 'bundle-cache')" in WRAPPER
    assert CS.index("using var pin = OpenVerifiedPin(cfg)") < CS.index("using var pw = ReadPassword()") < CS.index("conn.Open();")
    assert "Hash(bytes) == cfg.CertificateSha256" in CS
    assert "X509CertificateLoader.LoadCertificate(bytes)" in CS
    assert "cert.RawData.AsSpan().SequenceEqual(bytes)" in CS
    assert "!cert.HasPrivateKey" in CS
    assert "cert.NotBefore.ToUniversalTime()" in CS and "cert.NotAfter.ToUniversalTime()" in CS
    assert "FileShare.Read" in CS


def test_no_auto_enrollment_or_windows_trust_changes():
    for code in (WRAPPER, CS):
        assert not re.search(r"Import-Certificate|certutil|X509Store|TrustServerCertificate\s*=\s*true|SslStream|RemoteCertificateValidationCallback", code, re.I)
    assert "DOTNET_BUNDLE_EXTRACT_BASE_DIR" in WRAPPER
    assert "DOTNET_STARTUP_HOOKS" in WRAPPER
    assert "UseShellExecute = $false" in WRAPPER
    assert "RedirectStandardOutput = $true" in WRAPPER
    assert "RedirectStandardInput = $false" in WRAPPER
    assert "RedirectStandardError = $false" in WRAPPER
    assert "Console.ReadKey(intercept: true)" in CS
    assert "new SecureString()" in CS and "new SqlCredential(cfg.SqlUser, pw)" in CS
    config = WRAPPER.split("$config = [ordered]@{", 1)[1].split("}", 1)[0]
    assert "Password" not in config and "SqlUser=" in config


def test_worker_reuses_exact_audited_profiles_and_checks_before_data():
    resources = {node.attrib["LogicalName"]: node.attrib["Include"] for node in PROJECT.findall(".//EmbeddedResource")}
    assert resources == {
        "ident.guard.sql": "../sql/guard_readonly.sql",
        "ident.schema.sql": "../sql/payments_daily_v1_schema.sql",
        "ident.payments.sql": "../sql/payments_daily_v1.sql",
    }
    assert CS.index("conn.Open();") < CS.index("CheckRights(cmd);") < CS.index("CheckSchema(cmd);") < CS.index("cmd.CommandText = sql;")
    for name in ("identity", "roles", "server_effective", "database_effective", "server_grants", "database_grants", "ownership", "payments_effective"):
        assert f'"{name}"' in CS
    assert "checks.Remove(reader.GetString(0))" in CS
    assert "reader.GetInt32(1) == 1" in CS
    assert "checks.Count == 0 && !reader.NextResult()" in CS
    assert 'cmd.Parameters.Add("@start_date", SqlDbType.Date)' in CS
    assert 'cmd.Parameters.Add("@end_date_exclusive", SqlDbType.Date)' in CS
    assert 'reader.GetString(3), reader.GetString(4), reader.GetString(5)' in CS
    assert "queryHash == cfg.QuerySha256" in CS
    assert "$result.query_sha256 -cne $QuerySha256" in WRAPPER


def test_connection_check_only_stops_before_financial_query_and_file():
    check = CS.index("if (cfg.CheckOnly)")
    assert CS.index("CheckSchema(cmd);") < check < CS.index("cmd.CommandText = sql;")
    assert "return 0;" in CS[check:CS.index("cmd.CommandText = sql;")]
    assert "check_only = true" in CS[check:CS.index("cmd.CommandText = sql;")]
    assert "$result.check_only -ne $CheckOnly" in WRAPPER
    for mode in ("Standard", "PinnedCertificate"):
        marker = f"SQL_TLS_CHECK=PASS; MODE={mode}; SQL_READONLY=PASS; SCHEMA=PASS; FINANCIAL_READ=NO"
        assert marker in PS
        assert "return" in PS[PS.index(marker):PS.index(marker) + 170]
    assert PS.index("MODE=Standard; SQL_READONLY=PASS") < PS.index("$cmd.CommandText = $sql")
    assert PS.index("MODE=PinnedCertificate; SQL_READONLY=PASS") < PS.index("$rows = @(")


def test_pinned_dependency_and_release_gate():
    packages = PROJECT.findall(".//PackageReference")
    assert len(packages) == 1 and packages[0].attrib == {
        "Include": "Microsoft.Data.SqlClient", "Version": "[7.1.2]"
    }
    assert PROJECT.findtext(".//TargetFramework") == "net10.0"
    assert PROJECT.findtext(".//RuntimeIdentifier") == "win-x64"
    assert PROJECT.findtext(".//PublishSingleFile") == "true"
    assert PROJECT.findtext(".//RestorePackagesWithLockFile") == "true"
    assert PROJECT.findtext(".//NuGetAuditMode") == "all"
    assert "NU1904" in PROJECT.findtext(".//WarningsAsErrors")
    config = ET.parse(ROOT / "sql-client/NuGet.Config").getroot()
    assert [e.attrib["value"] for e in config.findall("./packageSources/add")] == ["https://api.nuget.org/v3/index.json"]
