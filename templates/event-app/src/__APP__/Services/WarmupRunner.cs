using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace __APP__.Services;

/// <summary>
/// Headless preflight, run as "__APP__.exe --warmup [--deadline seconds] [--full]" by the harness.
///
/// Works to a deadline so the whole build stays inside its time budget:
/// 1. Load the pinned model. The first load on a device compiles it into the shared cache
///    (%LOCALAPPDATA%\ZavaLiveBuild\ov-cache); every later app reuses it.
/// 2. Rehearse the first sample of every mode live and record it for the labelled replay.
/// 3. Rehearse the remaining samples while time remains (all of them with --full).
/// 4. Compile the backup model last, unless an earlier app already did.
/// 5. Write %LOCALAPPDATA%\__APP__\warmup.json for the harness Go/No-Go board.
/// </summary>
public static class WarmupRunner
{
    public static async Task<int> RunAsync(string[] args)
    {
        var clock = Stopwatch.StartNew();
        var deadline = TimeSpan.FromSeconds(ReadDeadline(args));
        var full = args.Any(a => a.Equals("--full", StringComparison.OrdinalIgnoreCase));
        bool TimeFor(double seconds) => full || clock.Elapsed + TimeSpan.FromSeconds(seconds) < deadline;

        var runtime = LocalAiRuntime.Instance;
        var modes = runtime.Config.EffectiveModes;
        var report = new Dictionary<string, object?>
        {
            ["startedAt"] = DateTime.Now,
            ["machine"] = Environment.MachineName,
            ["deadlineSeconds"] = deadline.TotalSeconds,
            ["modes"] = modes.Select(m => m.Id).ToList()
        };
        var ok = false;
        var runs = new List<object>();
        var replay = new List<ReplayEntry>();

        try
        {
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
            report["initSeconds"] = Math.Round(clock.Elapsed.TotalSeconds, 1);

            var first = modes.Where(m => m.SamplePrompts.Count > 0).Select(m => (mode: m, prompt: m.SamplePrompts[0])).ToList();
            var rest = modes.SelectMany(m => m.SamplePrompts.Skip(1).Select(p => (mode: m, prompt: p))).ToList();
            double avgSeconds = 30;

            async Task Rehearse((ModeConfig mode, string prompt) item)
            {
                var sw = Stopwatch.StartNew();
                var request = runtime.Engine.Build(item.mode, item.prompt, null, runtime.Config.ContextFields,
                    item.mode.DefaultLanguage, DateTime.Now);
                var result = await RunPromptAsync(runtime, request);
                runs.Add(result.summary);
                if (result.entry is not null)
                    replay.Add(result.entry);
                avgSeconds = (avgSeconds + sw.Elapsed.TotalSeconds) / 2;
            }

            var primaryOnNpu = runtime.IsLive && runtime.Device == "NPU" && !runtime.UsingBackup;
            if (runtime.IsLive)
            {
                foreach (var item in first)
                    await Rehearse(item);

                // More rehearsals while time allows, keeping enough back to compile the backup model.
                var backupNeedsCompile = runtime.Config.Backup is { } b && !AppSettings.Current.NpuCompiledModels.Contains(b.ModelId);
                var reserve = backupNeedsCompile ? 150 : 0;
                foreach (var item in rest)
                {
                    if (!runtime.IsLive || !TimeFor(avgSeconds + 10 + reserve))
                        break;
                    await Rehearse(item);
                }

                // Only the pinned primary may define the safety net.
                if (replay.Count > 0)
                    runtime.Replay.Save(replay);

                // Backup last: switching models in one process does not release NPU memory reliably.
                if (!runtime.UsingBackup)
                    report["backup"] = await WarmBackupAsync(runtime, TimeFor);
            }
            report["samples"] = runs;
            report["replayRecorded"] = replay.Count;
            ok = primaryOnNpu && replay.Count > 0 && first.All(f => replay.Any(r => r.Prompt.StartsWith($"[{f.mode.Name}")));
        }
        catch (Exception ex)
        {
            report["error"] = ex.Message;
            AppLog.Write($"Warm-up failed: {ex}");
        }

        report["ok"] = ok;
        report["finishedAt"] = DateTime.Now;
        report["seconds"] = Math.Round(clock.Elapsed.TotalSeconds);
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

    private static async Task<object?> WarmBackupAsync(LocalAiRuntime runtime, Func<double, bool> timeFor)
    {
        if (runtime.Config.Backup is not { } backup)
            return null;
        if (AppSettings.Current.NpuCompiledModels.Contains(backup.ModelId))
            return new { alias = backup.Alias, modelId = backup.ModelId, device = "NPU", live = true, loadSeconds = 0, skipped = true };
        if (!timeFor(150))
            return new { alias = backup.Alias, modelId = backup.ModelId, device = "-", live = false, loadSeconds = 0, skipped = false, note = "not compiled: out of time" };

        await runtime.LoadAsync(backup, null);
        return new
        {
            alias = backup.Alias,
            modelId = runtime.ModelId,
            device = runtime.Device,
            live = runtime.IsLive,
            loadSeconds = Math.Round(runtime.LoadMs / 1000, 1),
            skipped = false
        };
    }

    private static double ReadDeadline(string[] args)
    {
        var i = Array.FindIndex(args, a => a.Equals("--deadline", StringComparison.OrdinalIgnoreCase));
        return i >= 0 && i + 1 < args.Length && double.TryParse(args[i + 1], out var s) ? Math.Max(60, s) : 720;
    }

    /// <summary>"--kb-check": retrieval only, no model. Writes kb-check.json so rehearsals can verify grounding in seconds.</summary>
    public static int RunKnowledgeCheck()
    {
        var engine = new RequestEngine();
        var config = AppConfig.Load();
        var checks = config.EffectiveModes.SelectMany(mode => mode.SamplePrompts.Select(q =>
        {
            var r = engine.Build(mode, q, null, config.ContextFields, mode.DefaultLanguage, DateTime.Now);
            return new
            {
                mode = mode.Name,
                question = q,
                promptChars = r.SystemPrompt.Length + r.ModelPrompt.Length,
                sources = r.Sources.Select(s => $"{s.Chunk.Document} > {s.Chunk.Section} ({s.Score:F2})").ToList()
            };
        })).ToList();
        File.WriteAllText(Path.Combine(AppPaths.Root, "kb-check.json"), JsonSerializer.Serialize(new
        {
            documents = engine.Knowledge.DocumentCount,
            passages = engine.Knowledge.PassageCount,
            indexMs = Math.Round(engine.Knowledge.IndexMs, 1),
            checks
        }, new JsonSerializerOptions { WriteIndented = true }));
        return engine.Knowledge.PassageCount == 0 || checks.All(c => c.sources.Count > 0) ? 0 : 1;
    }

    private static async Task<(object summary, ReplayEntry? entry)> RunPromptAsync(LocalAiRuntime runtime, AppRequest request)
    {
        var prompt = request.ReplayKey;
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

        await foreach (var update in runtime.GenerateAsync(request, CancellationToken.None))
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
            modeId = request.Mode.Id,
            live,
            mode = last?.Mode.ToString(),
            tokens = last?.Tokens ?? 0,
            tokensPerSecond = Math.Round(last?.TokensPerSecond ?? 0, 1),
            firstTokenMs = Math.Round(last?.FirstTokenMs ?? 0),
            peakNpuPercent = Math.Round(runtime.NpuMonitor.PeakPercent),
            note = last?.Note,
            sources = request.Sources.Select(s => $"{s.Chunk.Document} > {s.Chunk.Section}").ToList(),
            output = AnswerCleaner.Clean(text.ToString())
        };
        var entry = live && !runtime.UsingBackup
            ? new ReplayEntry(prompt, AnswerCleaner.Clean(text.ToString()), runtime.ModelId, runtime.Device, DateTime.Now, last!.TokensPerSecond)
            : null;
        return (summary, entry);
    }
}
