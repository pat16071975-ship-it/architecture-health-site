# IDENT-006D - private research connector (SOURCE-ONLY)

Separate component. No modification to IDENT-004, IDENT-006A/B/C, AZ BAZE or MD Control. No real data, host identifiers, SQL passwords, tunnel keys or production settings in this public repository.

Target: a locally installed dedicated Windows service account launches a stdio MCP child using the official ModelContextProtocol SDK 2.4.0. An authorized, separately installed official OpenAI Secure MCP Tunnel client transports read-only calls via outbound HTTPS. No SQL or MCP inbound listening socket; no generic remote SQL, filesystem, shell, patient-data, configuration or mutation tool. No Google Drive or other intermediate reporting store. Private connector creation, workspace/tunnel permissions, tenant-specific authorization, network reachability and supported client build are unverified. Do not connect real data until these and data-processing authorization are separately confirmed. This candidate does not include the SCM/tunnel installer.

Initial catalog:
- ident_status: metadata only, no SQL connection.
- ident_specialist_performance: current schedule slots, union of actual completed reception intervals, future booked intervals, reserved time, reception counts and net rendered-service revenue, total vs linked to completed receptions by the same staff member. All times in seconds; reserve time remains included in gross schedule; do not subtract reserves blindly. Use linked revenue for actual-hour rate and preserve total revenue separately. No forecast for zero actual hours. Future bookings are planned, not guaranteed. No historical snapshots inferred. Incomplete appeared visits reported separately. Null revenue or missing orders fail closed.
- ident_primary_repeat: two definitions by first completed reception in database history; both visit and unique patient counts and linked revenue, with full revenue controls. Primary and repeat unique counts can overlap. No assertion of equivalence to native IDENT's report. Patient IDs used only within SQL joins/partitions, not selected in returned rows.

Query boundaries: inclusive local date from, exclusive local date to; 1-31 days, current SQL clock captured once, checked against configured IANA timezone; factual cutoff=min(observed,end), clamped to start. Current database state, not as-of recovery. Decimal 28,6 service sums in RUB (amount after discount, not payments); calculation rounding to 6 decimal places as in prior one-off report. Department mapping is NOT inferred; actual IDENT NSI fields were empty in the pilot. Staff classification must be separately confirmed. Source rows with missing staff identifiers/name fail closed. No patient or clinical text output. Reports return data and provenance, not executable instructions.

SQL baseline from successful one-off research queries, parameterized and restricted to SQL Server 2014 / compatibility 110 syntax. No data modifications. TLS exact DER pin and audited login guard reused from IDENT-004/006C; additional schema and object permission checks. DPAPI machine-scoped encryption bound to independent config and purpose, protected NTFS layout adapted from IDENT-006C, no shared state or tokens. Provisioning requires a new pre-created protected layout with dedicated AzIdentResearch virtual service account; not installed by this source candidate. Parent SCM/tunnel runtime, scoped runtime key, egress, restart, installer, resolution of the date discrepancy below, independent audit and real end-to-end acceptance remain required before publication or enablement. Do not reuse 006C secrets, seed, queue or service.

## Source-only laboratory

The approved part-2 scope adds dependency locks and synthetic tests without changing any existing production C# or SQL. CI verifies their ten Git blob IDs against source baseline `56a18a74095028646ba975dfa4a963a480a20350`. The .NET SDK is pinned in `global.json` to `10.0.404`. Both projects use the same package graph: SqlClient `7.1.2`, ModelContextProtocol `2.4.0` and Hosting `10.0.15` plus their locked transitive dependencies. Both committed lock files are the exact bytes from successful original CI run `45960373582`, SHA256 `73a8da26a0d3e14d58effd65f73ea8df4dbac87c68f484751da1e50be448a17a`. Restore runs in locked mode with dependency audit warnings treated as errors; CI does not generate or upgrade a missing lock.

`tests/AzIdentResearch.Lab.csproj` is a separate executable. It compiles the unchanged source files and embeds the unchanged query resources, selecting a test-only entry point. It is not a deployment package. It refuses a host with the production store present or with the service identity. There is no test switch, injected connection string or identity bypass in the production executable.

The Windows CI runner uses its preinstalled LocalDB runtime. The laboratory creates and removes a uniquely named, private LocalDB instance and synthetic database at compatibility level 110. It does not install a Windows service or accept a remote database target. SQL tests execute the embedded report queries against controlled fixtures and check independent expected aggregates. The separate MCP checks use the real SDK over a child process's stdio: initialize, tool discovery, schemas and annotations, status, invalid arguments, unavailable report calls and startup refusal under an ordinary identity. A successful report through MCP to a protected SQL connection is outside these tests.

Run only on an isolated Windows x64 test host with preinstalled LocalDB, from this module directory:

```powershell
dotnet restore tests/AzIdentResearch.Lab.csproj --runtime win-x64 --locked-mode --configfile NuGet.Config -p:Configuration=Release -p:SelfContained=true -p:TreatWarningsAsErrors=true
dotnet publish tests/AzIdentResearch.Lab.csproj -c Release -r win-x64 --self-contained true --no-restore -p:TreatWarningsAsErrors=true -o lab-output
./lab-output/AzIdentResearch.Lab.exe --lab
```

The workflow also builds the production executable and runs its existing self-tests. The `IDENT-006D-source-lab-evidence` artifact contains source identities, both locks, dependency inventory and test logs. A green source laboratory run is not production acceptance: LocalDB does not exercise the real SQL server's TLS pin, restricted login, protected store, service recovery or Secure MCP Tunnel. Production TLS and identity guards are unchanged and remain mandatory.

## Known date discrepancy — business decision still required

The two existing queries differ when a reception's planned start is inside the reporting month but its actual completion precedes that month. `primary_repeat.sql` requires `PlanStart >= @date_from` and `ReceptionEnded <= @fact_end`; `specialist_performance.sql` additionally requires `ReceptionEnded > @date_from` for completed visits and linked completed revenue. A rendered service dated inside the month can consequently be counted as linked in one report and not in the other.

The laboratory contains a characterization test for this exact discrepancy. It must reproduce the two different results and emit `KNOWN_DATE_DISCREPANCY=REPRODUCED` with `BUSINESS_DECISION=UNRESOLVED`. That marker records an unresolved result even when the laboratory itself passes. The fixture has planned start on 10 November, actual completion on 31 October and a rendered service of 100 RUB on 10 November: performance returns zero completed visits and zero linked amount, while primary/repeat returns one visit and 100 RUB linked; total rendered amount is 100 RUB in both. The test does not choose which date is authoritative, rewrite either query or assert agreement with IDENT's native report. The data-period rule requires a separate owner decision before a source correction.

First acceptance: user requests October/November primary/repeat report, then November load report from a different device with no terminal actions. Only declare complete when actual ChatGPT call returns current IDENT aggregates and totals reconcile. Additional report templates can be audited and deployed independently, never accept unrestricted SQL.

References: https://developers.openai.com/api/docs/guides/secure-mcp-tunnels ; https://github.com/openai/tunnel-client ; https://github.com/modelcontextprotocol/csharp-sdk/tree/v2.4.0 .
