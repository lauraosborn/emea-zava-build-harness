using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace __APP__.Services;

/// <summary>
/// Headless preflight, run as "__APP__.exe --warmup" by the Zava Live Build harness.
///
/// 1. Starts Foundry Local and loads the pinned primary model on the NPU. The first
///    load compiles the model into this app's OpenVINO cache, so on stage it loads
///    in seconds instead of minutes.
/// 2. Runs every sample prompt live and records the outputs for the labelled
///    replay safety net.
/// 3. Loads the backup model on the NPU once so it is compiled too.
/// 4. Writes %LOCALAPPDATA%\__APP__\warmup.json for the harness Go/No-Go board.
/// </summary>
public static class WarmupRunner
{
    public static async Task<int> RunAsync()
    {
        var runtime = LocalAiRuntime.Instance;
        var report = new Dictionary<string, object?>
        {
            ["startedAt"] = DateTime.Now,
            ["machine"] = Environment.MachineName
        };
        var ok = false;

        try
        {
            var sw = Stopwatch.StartNew();
            await runtime.InitializeAsync();
            report["npu"] = runtime.Telemetry.NpuName;
            report["npuDetected"] = runtime.Telemetry.NpuDetected;
            report["cpu"] = runtime.Telemetry.CpuName;
            report["primary"] = new
            {
                alias = runtime.ModelAlias,
                modelId = runtime.ModelId,
                device = runtime.Device,
                live = runtime.IsLive,
                usingBackup = runtime.UsingBackup,
                loadSeconds = Math.Round(runtime.LoadMs / 1000, 1),
                status = runtime.Status
            };
            report["initSeconds"] = Math.Round(sw.Elapsed.TotalSeconds, 1);

            var runs = new List<object>();
            var replay = new List<ReplayEntry>();
            if (runtime.IsLive)
            {
                foreach (var prompt in runtime.Config.SamplePrompts)
                {
                    var result = await RunPromptAsync(runtime, prompt);
                    runs.Add(result.summary);
                    if (result.entry is not null)
                        replay.Add(result.entry);
                }
                // Only the pinned primary may define the safety net, so a degraded
                // warm-up never replaces good recordings with backup-model output.
                if (replay.Count > 0 && !runtime.UsingBackup)
                    runtime.Replay.Save(replay);
            }
            report["samples"] = runs;
            report["replayRecorded"] = runtime.UsingBackup ? 0 : replay.Count;
            ok = runtime.IsLive && runtime.Device == "NPU" && !runtime.UsingBackup && replay.Count > 0;

            if (runtime.Config.Backup is { } backup && runtime.IsLive && !runtime.UsingBackup)
            {
                await runtime.LoadAsync(backup, null);
                object? backupRun = null;
                if (runtime.IsLive && runtime.Config.SamplePrompts.Count > 0)
                    backupRun = (await RunPromptAsync(runtime, runtime.Config.SamplePrompts[0])).summary;
                report["backup"] = new
                {
                    alias = backup.Alias,
                    modelId = runtime.ModelId,
                    device = runtime.Device,
                    live = runtime.IsLive,
                    loadSeconds = Math.Round(runtime.LoadMs / 1000, 1),
                    run = backupRun
                };
            }
        }
        catch (Exception ex)
        {
            report["error"] = ex.Message;
            AppLog.Write($"Warm-up failed: {ex}");
        }

        report["ok"] = ok;
        report["finishedAt"] = DateTime.Now;
        try
        {
            File.WriteAllText(AppPaths.WarmupReport,
                JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }));
        }
        catch (Exception ex)
        {
            AppLog.Write($"Could not write warm-up report: {ex.Message}");
        }
        return ok ? 0 : 1;
    }

    private static async Task<(object summary, ReplayEntry? entry)> RunPromptAsync(LocalAiRuntime runtime, string prompt)
    {
        var text = new StringBuilder();
        GenerationUpdate? last = null;
        using var sampling = new CancellationTokenSource();
        var sampler = Task.Run(async () =>
        {
            while (!sampling.IsCancellationRequested)
            {
                runtime.NpuMonitor.Sample();
                try { await Task.Delay(250, sampling.Token); } catch (OperationCanceledException) { }
            }
        });

        await foreach (var update in runtime.GenerateAsync(prompt, CancellationToken.None))
        {
            text.Append(update.Text);
            last = update;
        }
        sampling.Cancel();
        await sampler;

        var live = last is not null && last.Mode != InferenceMode.Replay;
        var summary = new
        {
            prompt,
            live,
            mode = last?.Mode.ToString(),
            tokens = last?.Tokens ?? 0,
            tokensPerSecond = Math.Round(last?.TokensPerSecond ?? 0, 1),
            firstTokenMs = Math.Round(last?.FirstTokenMs ?? 0),
            peakNpuPercent = Math.Round(runtime.NpuMonitor.PeakPercent),
            note = last?.Note
        };
        var entry = live
            ? new ReplayEntry(prompt, text.ToString().Trim(), runtime.ModelId, runtime.Device, DateTime.Now, last!.TokensPerSecond)
            : null;
        return (summary, entry);
    }
}
