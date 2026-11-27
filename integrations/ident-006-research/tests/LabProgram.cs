using System.Reflection;
using System.Security.Principal;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using ModelContextProtocol.Server;

// This entry point is compiled only into the separate lab executable. No lab
// switch, identity override or SQL connection injection exists in production.
internal static class LabProgram
{
    internal static void RequireIsolatedHost()
    {
        if (!OperatingSystem.IsWindows() || !Environment.Is64BitProcess)
            throw new InvalidOperationException("LAB_WINDOWS_X64_REQUIRED");
        if (Directory.Exists(PrivateStore.Root) || File.Exists(PrivateStore.Root))
            throw new InvalidOperationException("LAB_REFUSES_PROVISIONED_HOST");

        bool denied = false;
        try { PrivateStore.AssertService(); }
        catch (Blocked e) when (e.Message == "SERVICE_IDENTITY_REQUIRED") { denied = true; }
        catch (IdentityNotMappedException) { denied = true; }
        if (!denied) throw new InvalidOperationException("LAB_REFUSES_SERVICE_IDENTITY");
    }

    private static async Task<int> Main(string[] args)
    {
        try
        {
            RequireIsolatedHost();
            if (args is ["--production-startup-probe"])
            {
                // Invoke the unchanged production entry point with one fixed
                // mode; the process must be rejected before protected storage.
                var entry = typeof(Program).GetMethod("Main", BindingFlags.Static | BindingFlags.NonPublic)
                    ?? throw new InvalidOperationException("PRODUCTION_ENTRYPOINT_MISSING");
                return await (Task<int>)(entry.Invoke(null, new object[] { new[] { "--stdio" } })
                    ?? throw new InvalidOperationException("PRODUCTION_ENTRYPOINT_RESULT"));
            }
            if (args is ["--mcp-fixture"])
            {
                // The protocol fixture uses the real tool types and engine.
                // Valid report calls still hit the unchanged service guard.
                var builder = Host.CreateApplicationBuilder(new HostApplicationBuilderSettings
                    { Args = Array.Empty<string>(), DisableDefaults = true });
                builder.Logging.ClearProviders();
                builder.Services.AddSingleton<ResearchEngine>();
                builder.Services.AddMcpServer().WithStdioServerTransport().WithTools<ResearchTools>();
                using var host = builder.Build();
                await host.RunAsync();
                return 0;
            }
            if (args is not ["--lab"])
                throw new InvalidOperationException("LAB_MODE_REQUIRED");

            await SqlLab.RunAsync();
            await McpLab.RunAsync();
            Console.WriteLine("IDENT_006D_LAB=PASS; DATA=SYNTHETIC; REAL_IDENT=NONE; SERVICE_INSTALL=NONE; TUNNEL=NONE; BUSINESS_RULE_CHANGE=NONE; PRODUCTION_READINESS=NOT_APPROVED");
            return 0;
        }
        catch (Exception e)
        {
            // Synthetic laboratory errors are useful in CI, but there are no
            // production credentials or clinical data in this executable.
            Console.Error.WriteLine("IDENT_006D_LAB=FAIL; " + e.GetType().Name + ": " + e.Message);
            return 1;
        }
    }
}
