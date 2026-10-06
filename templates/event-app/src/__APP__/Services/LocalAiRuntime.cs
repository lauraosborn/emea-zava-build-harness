using System.Diagnostics;
using Microsoft.AI.Foundry.Local;
using Microsoft.Extensions.Logging.Abstractions;

namespace __APP__.Services;

public enum RuntimeState
{
    Starting,
    Downloading,
    Preparing,
    Ready,
    Replay
}

public enum InferenceMode
{
    LiveNpu,
    LiveGpu,
    Replay
}

public sealed record GenerationUpdate(
    string Text,
    int Tokens,
    double TokensPerSecond,
    double FirstTokenMs,
    double ElapsedMs,
    string Model,
    InferenceMode Mode,
    string? Note = null);

public sealed record InferenceRecord(
    DateTime At,
    string Model,
    InferenceMode Mode,
    int Tokens,
    double TokensPerSecond,
    double FirstTokenMs,
    double PeakNpuPercent);

/// <summary>
/// Owns the in-process Foundry Local runtime for the live demo.
///
/// Stage-safe inference hierarchy:
///   1. Pinned primary model on the NPU
///   2. Pinned backup model on the NPU (small, fast)
///   3. GPU, only after the presenter explicitly consents
///   4. Replay of outputs recorded on this device, clearly labelled
/// A first-token watchdog moves a stalled request to replay, so the stage never hangs.
/// </summary>
public sealed class LocalAiRuntime
{
    public static LocalAiRuntime Instance { get; } = new();

    private ICatalog? _catalog;
    private Task? _init;
    private readonly SemaphoreSlim _switch = new(1, 1);

    public event Action? Changed;

    public AppConfig Config { get; } = AppConfig.Load();
    public NpuTelemetryService Telemetry { get; } = new(AppPaths.Telemetry);
    public NpuUtilisationMonitor NpuMonitor { get; } = new();
    public GpuFallbackConsentService Consent { get; } = new();
    public ReplayLibrary Replay { get; } = ReplayLibrary.Load();
    public RequestEngine Engine { get; } = new();

    public IInferenceService Llm { get; private set; }
    public RuntimeState State { get; private set; } = RuntimeState.Starting;
    public string Status { get; private set; } = "Starting Foundry Local...";
    public string ModelAlias { get; private set; }
    public string ModelId { get; private set; }
    public string Device { get; private set; } = "-";
    public string ExecutionProvider { get; private set; }
    public double LoadMs { get; private set; }
    public bool UsingBackup { get; private set; }

    /// <summary>Presenter safety net (Ctrl+R): answer from recorded outputs, labelled REPLAY.</summary>
    public bool ForceReplay { get; set; }

    public bool IsGenerating { get; private set; }
    public int LiveTokenCount { get; private set; }
    public double LastTokensPerSecond { get; private set; }
    public double LastFirstTokenMs { get; private set; }
    public List<InferenceRecord> History { get; } = new();

    public bool IsLive => Llm is FoundryLocalChatService { IsReady: true };

    private LocalAiRuntime()
    {
        Llm = new DemoModeService(Replay);
        ModelAlias = Config.Primary.Alias;
        ModelId = Config.Primary.ModelId;
        ExecutionProvider = Config.Primary.ExecutionProvider;
    }

    public Task InitializeAsync() => _init ??= InitializeCoreAsync();

    private async Task InitializeCoreAsync()
    {
        try
        {
            await Telemetry.DetectNpuAsync();
            Raise();
            await Task.Run(MirrorFoundryCliModels);

            Status = "Starting Foundry Local runtime...";
            Raise();
            await FoundryLocalManager.CreateAsync(new Configuration
            {
                AppName = "__APP__",
                AppDataDir = AppPaths.Root,
                ModelCacheDir = AppPaths.Models,
                LogsDir = AppPaths.Logs,
                LogLevel = Microsoft.AI.Foundry.Local.LogLevel.Warning
            }, NullLogger.Instance);

            Status = "Registering NPU execution providers...";
            Raise();
            try
            {
                await FoundryLocalManager.Instance.DownloadAndRegisterEpsAsync();
            }
            catch (Exception ex)
            {
                // Offline on stage is fine once preflight has registered the EPs.
                AppLog.Write($"EP registration warning: {ex.Message}");
            }
            _catalog = await FoundryLocalManager.Instance.GetCatalogAsync();
        }
        catch (Exception ex)
        {
            AppLog.Write($"Foundry Local start failed: {ex}");
            EnterReplay($"Foundry Local could not start ({ex.Message})");
            return;
        }

        await LoadAsync(Config.Primary, Config.Backup);
    }

    /// <summary>Load the primary model NPU-first, then the backup on the NPU, then consent-gated GPU, then replay.</summary>
    public async Task LoadAsync(ModelRef primary, ModelRef? backup)
    {
        if (_catalog is null)
            return;

        await _switch.WaitAsync();
        try
        {
            if (Llm is FoundryLocalChatService previous)
            {
                try { await previous.UnloadAsync(); } catch (Exception ex) { AppLog.Write($"Unload failed: {ex.Message}"); }
            }
            Llm = new DemoModeService(Replay);
            State = RuntimeState.Preparing;
            Raise();

            var selector = new InferenceHardwareSelector(new HardwareCapabilityService(), Consent, Telemetry);
            var sw = Stopwatch.StartNew();
            var service = await selector.SelectAsync(
                primary.Alias,
                npuFactory: async (_, ct) =>
                {
                    try
                    {
                        return await LoadVariantAsync(primary, HardwareTier.Npu, isBackup: false, ct);
                    }
                    catch (Exception ex) when (backup is not null)
                    {
                        AppLog.Write($"Primary NPU load failed ({ex.Message}); trying backup {backup.Alias} on the NPU.");
                        return await LoadVariantAsync(backup, HardwareTier.Npu, isBackup: true, ct);
                    }
                },
                gpuFactory: (_, ct) => LoadVariantAsync(primary, HardwareTier.Gpu, isBackup: false, ct));

            LoadMs = sw.Elapsed.TotalMilliseconds;
            if (service is FoundryLocalChatService chat)
            {
                Llm = chat;
                ModelId = chat.ModelId;
                State = RuntimeState.Ready;
                Status = $"Ready: {ModelAlias} on this device ({Device}, {LoadMs / 1000:F1}s)";
                if (chat.ActiveTier == HardwareTier.Npu && !AppSettings.Current.NpuCompiledModels.Contains(chat.ModelId))
                {
                    AppSettings.Current.NpuCompiledModels.Add(chat.ModelId);
                    AppSettings.Current.Save();
                }
                AppLog.Write(Status);
            }
            else
            {
                EnterReplay("the on-device model could not be loaded");
            }
        }
        catch (Exception ex)
        {
            AppLog.Write($"Model load failed: {ex}");
            EnterReplay(ex.Message);
        }
        finally
        {
            _switch.Release();
            Raise();
        }
    }

    private async Task<IInferenceService> LoadVariantAsync(ModelRef target, HardwareTier tier, bool isBackup, CancellationToken ct)
    {
        var deviceType = tier == HardwareTier.Npu ? DeviceType.NPU : DeviceType.GPU;
        var model = await _catalog!.GetModelAsync(target.Alias)
                    ?? throw new InvalidOperationException($"'{target.Alias}' is not in the Foundry Local catalog.");

        // Use the exact pinned build if it exists; otherwise the newest build for this device type.
        var variant = model.Variants.FirstOrDefault(v =>
                          string.Equals(v.Id, target.ModelId, StringComparison.OrdinalIgnoreCase) &&
                          v.Info.Runtime?.DeviceType == deviceType)
                      ?? model.Variants.FirstOrDefault(v => v.Info.Runtime?.DeviceType == deviceType)
                      ?? throw new InvalidOperationException($"No {deviceType} build of {target.Alias} is published.");

        model.SelectVariant(variant);
        ModelAlias = target.Alias;
        ModelId = variant.Id;
        UsingBackup = isBackup;
        Device = tier == HardwareTier.Npu ? "NPU" : "GPU (approved)";
        ExecutionProvider = tier == HardwareTier.Npu ? target.ExecutionProvider : "GPU";

        var firstCompile = tier == HardwareTier.Npu && !AppSettings.Current.NpuCompiledModels.Contains(variant.Id);
        Status = firstCompile
            ? $"Preparing {target.Alias} for this device. First time only, 1 to 3 minutes."
            : $"Loading {target.Alias} on the {Device}{(isBackup ? " (backup model)" : "")}...";
        Raise();

        var service = new FoundryLocalChatService(model, tier);
        await service.LoadModelAsync(variant.Id, ct);
        return service;
    }

    private void EnterReplay(string reason)
    {
        Llm = new DemoModeService(Replay);
        State = RuntimeState.Replay;
        Device = "Replay";
        Status = Replay.Entries.Count > 0
            ? $"Replay mode: {reason}. Recorded on-device outputs are available and labelled."
            : $"Replay mode: {reason}. Run preflight to record on-device outputs.";
        AppLog.Write(Status);
        Raise();
    }

    public void ReportDownload(float percent)
    {
        State = RuntimeState.Downloading;
        Status = $"Downloading {ModelAlias}: {percent:F0}%";
        Raise();
    }

    /// <summary>Stream a completion with a first-token watchdog. Stalls fall back to labelled replay.</summary>
    public IAsyncEnumerable<GenerationUpdate> GenerateAsync(string prompt, CancellationToken ct) =>
        GenerateAsync(prompt, Config.SystemPrompt, prompt, ct);

    public IAsyncEnumerable<GenerationUpdate> GenerateAsync(AppRequest request, CancellationToken ct) =>
        GenerateAsync(request.ModelPrompt, request.SystemPrompt, request.ReplayKey, ct);

    /// <param name="modelPrompt">The grounded prompt sent to the on-device model.</param>
    /// <param name="replayKey">The short key used to find a recorded output in the replay safety net.</param>
    public async IAsyncEnumerable<GenerationUpdate> GenerateAsync(
        string modelPrompt,
        string systemPrompt,
        string replayKey,
        [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken ct)
    {
        IInferenceService service = ForceReplay ? new DemoModeService(Replay) : Llm;
        var mode = service.ActiveTier switch
        {
            HardwareTier.Npu => InferenceMode.LiveNpu,
            HardwareTier.Gpu => InferenceMode.LiveGpu,
            _ => InferenceMode.Replay
        };
        var prompt = mode == InferenceMode.Replay ? replayKey : modelPrompt;
        var options = new InferenceOptions
        {
            SystemPrompt = systemPrompt,
            MaxTokens = Config.MaxTokens,
            Temperature = Config.Temperature
        };

        var sw = Stopwatch.StartNew();
        double firstTokenMs = 0;
        string? fallbackReason = null;
        IsGenerating = true;
        LiveTokenCount = 0;
        LastTokensPerSecond = 0;
        NpuMonitor.ResetPeak();
        Raise();

        using var watchdog = CancellationTokenSource.CreateLinkedTokenSource(ct);
        if (mode != InferenceMode.Replay)
            watchdog.CancelAfter(TimeSpan.FromSeconds(Math.Max(5, Config.FirstTokenTimeoutSeconds)));

        var stream = service.StreamAsync(prompt, options, watchdog.Token).GetAsyncEnumerator(watchdog.Token);
        var generated = new System.Text.StringBuilder();
        var busyRetried = false;
        var stopEarly = false;
        try
        {
            while (true)
            {
                bool hasNext;
                try
                {
                    hasNext = await stream.MoveNextAsync();
                }
                catch (OperationCanceledException) when (!ct.IsCancellationRequested)
                {
                    fallbackReason = LiveTokenCount == 0
                        ? $"No first token within {Config.FirstTokenTimeoutSeconds}s"
                        : null;
                    break;
                }
                catch (Exception ex) when (!ct.IsCancellationRequested && LiveTokenCount == 0 && !busyRetried &&
                                           ex.Message.Contains("busy", StringComparison.OrdinalIgnoreCase))
                {
                    // The previous answer is still finishing on the device: wait a moment and try once more.
                    AppLog.Write("Model busy finishing the previous answer; retrying once.");
                    busyRetried = true;
                    await stream.DisposeAsync();
                    await Task.Delay(1500, ct);
                    stream = service.StreamAsync(prompt, options, watchdog.Token).GetAsyncEnumerator(watchdog.Token);
                    continue;
                }
                catch (Exception ex) when (!ct.IsCancellationRequested)
                {
                    AppLog.Write($"Generation failed: {ex}");
                    fallbackReason = $"Live generation failed ({ex.Message})";
                    break;
                }

                if (!hasNext)
                    break;

                if (LiveTokenCount == 0)
                {
                    firstTokenMs = sw.Elapsed.TotalMilliseconds;
                    LastFirstTokenMs = firstTokenMs;
                    watchdog.CancelAfter(Timeout.InfiniteTimeSpan);
                }
                LiveTokenCount++;
                var genSeconds = Math.Max(0.001, (sw.Elapsed.TotalMilliseconds - firstTokenMs) / 1000.0);
                LastTokensPerSecond = LiveTokenCount > 1 ? (LiveTokenCount - 1) / genSeconds : 0;
                yield return new GenerationUpdate(stream.Current, LiveTokenCount, LastTokensPerSecond,
                    firstTokenMs, sw.Elapsed.TotalMilliseconds, service.ModelName, mode);

                generated.Append(stream.Current);
                if (mode != InferenceMode.Replay && IsLooping(generated))
                {
                    AppLog.Write("Repetition guard stopped a looping answer.");
                    yield return new GenerationUpdate("\n\n*Stopped early by the repetition guard.*", LiveTokenCount, LastTokensPerSecond,
                        firstTokenMs, sw.Elapsed.TotalMilliseconds, service.ModelName, mode);
                    stopEarly = true;
                    break;
                }
            }
        }
        finally
        {
            if (stopEarly)
            {
                // Stop generation on the device too, so the next request does not find it busy.
                try { watchdog.Cancel(); } catch (ObjectDisposedException) { }
            }
            try { await stream.DisposeAsync(); }
            catch (Exception ex) when (ex is OperationCanceledException || stopEarly) { }
            IsGenerating = false;
            Record(service, mode, firstTokenMs, sw.Elapsed.TotalMilliseconds);
        }

        if (fallbackReason is not null)
        {
            var replay = new DemoModeService(Replay);
            var note = $"{fallbackReason}. Showing a recorded on-device output instead.";
            AppLog.Write(note);
            var tokens = 0;
            await foreach (var token in replay.StreamAsync(replayKey, options, ct))
            {
                tokens++;
                yield return new GenerationUpdate(token, tokens, 0, 0, sw.Elapsed.TotalMilliseconds,
                    replay.ModelName, InferenceMode.Replay, note);
            }
        }
    }

    /// <summary>
    /// Small models can fall into a loop ("12. Check... 13. Check..."). If the last stretch of
    /// text (ignoring numbers) has already appeared twice before, the answer is stuck: stop it.
    /// </summary>
    private static bool IsLooping(System.Text.StringBuilder generated)
    {
        if (generated.Length < 200)
            return false;
        var text = new string(generated.ToString().Where(c => !char.IsDigit(c)).ToArray());
        var tail = text[^40..];
        var count = 0;
        var index = 0;
        while ((index = text.IndexOf(tail, index, StringComparison.Ordinal)) >= 0)
        {
            if (++count >= 3)
                return true;
            index++;
        }
        return false;
    }

    private void Record(IInferenceService service, InferenceMode mode, double firstTokenMs, double elapsedMs)
    {
        try
        {
            History.Insert(0, new InferenceRecord(DateTime.Now, mode == InferenceMode.Replay ? "replay" : ModelAlias,
                mode, LiveTokenCount, LastTokensPerSecond, firstTokenMs, NpuMonitor.PeakPercent));
            if (History.Count > 8)
                History.RemoveAt(History.Count - 1);
            Telemetry.RecordInference(mode == InferenceMode.Replay ? "replay" : ModelId, elapsedMs,
                LastTokensPerSecond, mode == InferenceMode.LiveNpu);
        }
        catch (Exception ex)
        {
            AppLog.Write($"Telemetry record failed: {ex.Message}");
        }
        Raise();
    }

    /// <summary>Hard-link any models the Foundry Local CLI already downloaded, before the catalog is read.</summary>
    private static void MirrorFoundryCliModels()
    {
        try
        {
            if (!Directory.Exists(AppPaths.FoundryCliCache))
                return;
            foreach (var publisher in Directory.EnumerateDirectories(AppPaths.FoundryCliCache))
                foreach (var model in Directory.EnumerateDirectories(publisher))
                {
                    var folder = Path.GetFileName(model);
                    var dash = folder.LastIndexOf('-');
                    if (dash > 0)
                        ModelCacheManager.TryMirrorFromFoundryCli(folder[..dash] + ":" + folder[(dash + 1)..]);
                }
        }
        catch (Exception ex)
        {
            AppLog.Write($"Model mirror scan failed: {ex.Message}");
        }
    }

    private void Raise() => Changed?.Invoke();
}
