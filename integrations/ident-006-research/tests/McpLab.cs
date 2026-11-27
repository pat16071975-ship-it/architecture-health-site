using System.Diagnostics;
using System.Reflection;
using System.Text;
using System.Text.Json;

internal static class McpLab
{
    private static int checks;
    private static void Check(bool ok, string name)
    {
        if (!ok) throw new InvalidOperationException("MCP_LAB_" + name);
        checks++;
    }

    private static Process Child(string mode)
    {
        var exe = Environment.ProcessPath ?? throw new InvalidOperationException("LAB_EXE_PATH");
        var start = new ProcessStartInfo(exe)
        {
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false),
            StandardOutputEncoding = new UTF8Encoding(false, true),
            StandardErrorEncoding = new UTF8Encoding(false, true)
        };
        if (string.Equals(Path.GetFileNameWithoutExtension(exe), "dotnet", StringComparison.OrdinalIgnoreCase))
            start.ArgumentList.Add(Assembly.GetExecutingAssembly().Location);
        start.ArgumentList.Add(mode);
        return Process.Start(start) ?? throw new InvalidOperationException("LAB_CHILD_START");
    }

    private static async Task Stop(Process process)
    {
        if (process.HasExited) return;
        process.StandardInput.Close();
        try { await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5)); }
        catch (TimeoutException)
        {
            // MCP stdio lifecycle allows closing stdin and then terminating a
            // server that does not exit. Kill only this spawned child tree.
            if (!process.HasExited) process.Kill(entireProcessTree: true);
            await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5));
        }
    }

    private static async Task<string> ReadBounded(StreamReader reader, int limit)
    {
        var text = new StringBuilder();
        var buffer = new char[2048];
        int read;
        while ((read = await reader.ReadAsync(buffer)) > 0)
        {
            if (text.Length + read > limit) throw new InvalidOperationException("MCP_LAB_OUTPUT_LIMIT");
            text.Append(buffer, 0, read);
        }
        return text.ToString();
    }

    private sealed class ProtocolClient : IAsyncDisposable
    {
        private readonly Process process = Child("--mcp-fixture");
        private readonly Task<string> stderr;
        private int nextId;
        internal ProtocolClient() => stderr = ReadBounded(process.StandardError, 64 * 1024);

        internal async Task Notify(string method)
        {
            await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(new { jsonrpc = "2.0", method }));
            await process.StandardInput.FlushAsync();
        }

        internal async Task<JsonElement> Request(string method, object parameters)
        {
            int id = ++nextId;
            await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(
                new { jsonrpc = "2.0", id, method, @params = parameters }));
            await process.StandardInput.FlushAsync();
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(20));
            for (int i = 0; i < 20; i++)
            {
                string? line = await process.StandardOutput.ReadLineAsync(timeout.Token);
                if (line is null || line.Length is 0 or > 256 * 1024)
                    throw new InvalidOperationException("MCP_LAB_STDOUT_FRAMING");
                using var doc = JsonDocument.Parse(line);
                var message = doc.RootElement;
                if (message.ValueKind != JsonValueKind.Object ||
                    message.GetProperty("jsonrpc").GetString() != "2.0")
                    throw new InvalidOperationException("MCP_LAB_STDOUT_NOT_JSONRPC");
                if (message.TryGetProperty("id", out var responseId))
                {
                    if (message.TryGetProperty("method", out var serverMethod))
                    {
                        if (serverMethod.GetString() != "ping")
                            throw new InvalidOperationException("MCP_LAB_UNNEGOTIATED_REQUEST");
                        await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(
                            new { jsonrpc = "2.0", id = responseId.Clone(), result = new { } }));
                        await process.StandardInput.FlushAsync();
                        continue;
                    }
                    if (responseId.ValueKind != JsonValueKind.Number || responseId.GetInt32() != id ||
                        message.TryGetProperty("result", out _) == message.TryGetProperty("error", out _))
                        throw new InvalidOperationException("MCP_LAB_RESPONSE_ENVELOPE");
                    return message.Clone();
                }
                if (!message.TryGetProperty("method", out _))
                    throw new InvalidOperationException("MCP_LAB_NOTIFICATION_ENVELOPE");
            }
            throw new InvalidOperationException("MCP_LAB_NOTIFICATION_LIMIT");
        }

        public async ValueTask DisposeAsync()
        {
            try
            {
                await Stop(process);
                var tail = await ReadBounded(process.StandardOutput, 64 * 1024);
                foreach (var line in tail.Split('\n', StringSplitOptions.RemoveEmptyEntries))
                {
                    using var doc = JsonDocument.Parse(line);
                    if (doc.RootElement.GetProperty("jsonrpc").GetString() != "2.0")
                        throw new InvalidOperationException("MCP_LAB_STDOUT_TAIL");
                }
                await stderr;
            }
            finally { process.Dispose(); }
        }
    }

    private static JsonElement Result(JsonElement response)
    {
        if (response.TryGetProperty("error", out var error))
            throw new InvalidOperationException("MCP_LAB_UNEXPECTED_PROTOCOL_ERROR: " + error.GetRawText());
        return response.GetProperty("result");
    }

    private static string Text(JsonElement result)
    {
        var content = result.GetProperty("content");
        if (content.ValueKind != JsonValueKind.Array || content.GetArrayLength() != 1 ||
            content[0].GetProperty("type").GetString() != "text")
            throw new InvalidOperationException("MCP_LAB_TEXT_RESULT");
        return content[0].GetProperty("text").GetString() ?? "";
    }

    private static bool IsError(JsonElement response) => response.TryGetProperty("error", out _) ||
        (response.TryGetProperty("result", out var result) &&
         result.TryGetProperty("isError", out var flag) && flag.ValueKind == JsonValueKind.True);

    private static async Task ToolError(ProtocolClient client, string name, object args, params string[] expected)
    {
        var response = await client.Request("tools/call", new { name, arguments = args });
        Check(IsError(response), name + "_MUST_FAIL");
        if (expected.Length == 0) return;
        string text = response.TryGetProperty("error", out var error)
            ? error.GetProperty("message").GetString() ?? "" : Text(Result(response));
        Check(expected.Any(code => text.Contains(code, StringComparison.Ordinal)) && text.Length < 2048 &&
            !text.Contains(" at ", StringComparison.Ordinal) && !text.Contains("Password", StringComparison.OrdinalIgnoreCase),
            name + "_SAFE_ERROR_CODE");
    }

    private static async Task Status(ProtocolClient client)
    {
        var response = await client.Request("tools/call", new { name = "ident_status", arguments = new { } });
        Check(!IsError(response), "STATUS_SUCCESS");
        using var doc = JsonDocument.Parse(Text(Result(response)));
        var status = doc.RootElement;
        Check(status.GetProperty("service").GetString() == "IDENT research" &&
              status.GetProperty("mode").GetString() == "read_only" &&
              status.GetProperty("sql_connection").GetString() == "not_checked" &&
              status.GetProperty("catalog").GetString() == "v1" &&
              status.GetProperty("reports").EnumerateArray().Select(x => x.GetString())
                .ToHashSet().SetEquals(new[] { "specialist_performance", "primary_repeat" }), "STATUS_SEMANTICS");
    }

    internal static async Task RunAsync()
    {
        LabProgram.RequireIsolatedHost();
        using (var startup = Child("--production-startup-probe"))
        {
            var stdout = ReadBounded(startup.StandardOutput, 64 * 1024);
            var stderr = ReadBounded(startup.StandardError, 64 * 1024);
            try
            {
                await startup.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(20));
                var err = await stderr;
                Check(startup.ExitCode == 1 && (await stdout).Length == 0 &&
                    (err.Contains("IDENT_RESEARCH_STOP=SERVICE_IDENTITY_REQUIRED", StringComparison.Ordinal) ||
                     err.Contains("IDENT_RESEARCH_STOP=LOCAL_RUNTIME", StringComparison.Ordinal)), "PRODUCTION_STARTUP_FAIL_CLOSED");
            }
            finally { await Stop(startup); }
        }

        await using (var client = new ProtocolClient())
        {
            var initialized = Result(await client.Request("initialize", new
            {
                protocolVersion = "2025-06-18", capabilities = new { },
                clientInfo = new { name = "IDENT-006D-synthetic-lab", version = "1.0.0" }
            }));
            Check(initialized.GetProperty("protocolVersion").GetString() == "2025-06-18" &&
                initialized.GetProperty("capabilities").TryGetProperty("tools", out _), "INITIALIZE");
            await client.Notify("notifications/initialized");
            var listing = Result(await client.Request("tools/list", new { }));
            var tools = listing.GetProperty("tools").EnumerateArray().ToDictionary(x => x.GetProperty("name").GetString()!);
            Check(tools.Keys.ToHashSet().SetEquals(new[] { "ident_status", "ident_specialist_performance", "ident_primary_repeat" }) &&
                (!listing.TryGetProperty("nextCursor", out var cursor) || cursor.ValueKind == JsonValueKind.Null), "FIXED_TOOL_CATALOG");
            foreach (var (name, tool) in tools)
            {
                var annotations = tool.GetProperty("annotations");
                Check(annotations.GetProperty("readOnlyHint").GetBoolean() &&
                    !annotations.GetProperty("destructiveHint").GetBoolean() &&
                    annotations.GetProperty("idempotentHint").GetBoolean() &&
                    !annotations.GetProperty("openWorldHint").GetBoolean(), name + "_ANNOTATIONS");
                var schema = tool.GetProperty("inputSchema");
                string[] expected = name switch
                {
                    "ident_status" => Array.Empty<string>(),
                    "ident_specialist_performance" => new[] { "date_from", "date_to_exclusive" },
                    _ => new[] { "staff_id", "date_from", "date_to_exclusive" }
                };
                var properties = schema.TryGetProperty("properties", out var props)
                    ? props.EnumerateObject().Select(x => x.Name).ToHashSet() : new HashSet<string>();
                var required = schema.TryGetProperty("required", out var req)
                    ? req.EnumerateArray().Select(x => x.GetString()!).ToHashSet() : new HashSet<string>();
                Check(schema.GetProperty("type").GetString() == "object" && properties.SetEquals(expected) &&
                    required.SetEquals(expected) && expected.All(key => props.GetProperty(key).GetProperty("type").GetString() ==
                        (key == "staff_id" ? "integer" : "string")), name + "_INPUT_SCHEMA");
            }
            await Status(client);
            await ToolError(client, "ident_specialist_performance", new { date_from = "2026-02-30", date_to_exclusive = "2026-03-01" }, "INVALID_DATE");
            await ToolError(client, "ident_specialist_performance", new { date_from = "2026-11-01", date_to_exclusive = "2026-11-01" }, "DATE_WINDOW_1_TO_31_DAYS");
            await ToolError(client, "ident_specialist_performance", new { date_from = "2026-11-01", date_to_exclusive = "2026-12-03" }, "DATE_WINDOW_1_TO_31_DAYS");
            await ToolError(client, "ident_specialist_performance", new { date_from = "2026-11-02", date_to_exclusive = "2026-11-01" }, "DATE_WINDOW_1_TO_31_DAYS");
            await ToolError(client, "ident_primary_repeat", new { staff_id = 0, date_from = "2026-11-01", date_to_exclusive = "2026-12-01" }, "STAFF_ID_INVALID");
            await ToolError(client, "ident_primary_repeat", new { staff_id = -1, date_from = "2026-11-01", date_to_exclusive = "2026-12-01" }, "STAFF_ID_INVALID");
            await ToolError(client, "ident_primary_repeat", new { date_from = "2026-11-01", date_to_exclusive = "2026-12-01" });
            await ToolError(client, "ident_primary_repeat", new { staff_id = "not-an-integer", date_from = "2026-11-01", date_to_exclusive = "2026-12-01" });
            await ToolError(client, "execute_sql", new { sql = "SELECT 1" });
            await ToolError(client, "ident_specialist_performance", new { date_from = "2026-11-01", date_to_exclusive = "2026-12-01" }, "SERVICE_IDENTITY_REQUIRED", "REPORT_UNAVAILABLE");
            await ToolError(client, "ident_primary_repeat", new { staff_id = 1, date_from = "2026-11-01", date_to_exclusive = "2026-12-01" }, "SERVICE_IDENTITY_REQUIRED", "REPORT_UNAVAILABLE");
            await Status(client);
        }
        Console.WriteLine($"MCP_LAB={checks}/{checks} PASS; TRANSPORT=SDK_STDIO; TOOLS=3; PRODUCTION_STARTUP=DENIED; SQL_CONNECTION=NONE; REAL_DATA=NONE; TUNNEL=NONE");
    }
}
