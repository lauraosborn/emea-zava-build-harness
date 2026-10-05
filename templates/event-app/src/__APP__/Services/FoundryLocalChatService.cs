using System.Runtime.CompilerServices;
using Betalgo.Ranul.OpenAI.ObjectModels.RequestModels;
using Microsoft.AI.Foundry.Local;

namespace __APP__.Services;

/// <summary>
/// IInferenceService backed by the Foundry Local SDK, running in-process on this device.
/// No REST endpoint, no network: the model is loaded and executed locally.
/// </summary>
public sealed class FoundryLocalChatService : IInferenceService
{
    private readonly IModel _model;
    private readonly SemaphoreSlim _gate = new(1, 1);
    private OpenAIChatClient? _chat;

    public FoundryLocalChatService(IModel model, HardwareTier tier)
    {
        _model = model;
        ActiveTier = tier;
    }

    public string ModelName => _model.Alias;
    public string ModelId => _model.Id;
    public string ModelVersion => ModelId.Contains(':') ? "v" + ModelId[(ModelId.LastIndexOf(':') + 1)..] : "v1";
    public bool IsReady { get; private set; }
    public bool IsNpuActive => ActiveTier == HardwareTier.Npu && IsReady;
    public bool IsGpuActive => ActiveTier == HardwareTier.Gpu && IsReady;
    public HardwareTier ActiveTier { get; }

    public async Task LoadModelAsync(string modelId, CancellationToken cancellationToken = default)
    {
        if (!await _model.IsCachedAsync(cancellationToken))
        {
            if (!ModelCacheManager.TryMirrorFromFoundryCli(_model.Id))
                await _model.DownloadAsync(p => LocalAiRuntime.Instance.ReportDownload(p), cancellationToken);
        }

        if (ActiveTier == HardwareTier.Npu)
            ModelCacheManager.EnableOpenVinoCompiledCache(await _model.GetPathAsync(cancellationToken));

        await _model.LoadAsync(cancellationToken);
        _chat = await _model.GetChatClientAsync(cancellationToken);
        IsReady = true;
    }

    public async Task UnloadAsync()
    {
        IsReady = false;
        _chat = null;
        await _model.UnloadAsync();
    }

    public async Task<string> CompleteAsync(string prompt, InferenceOptions? options = null, CancellationToken cancellationToken = default)
    {
        var sb = new System.Text.StringBuilder();
        await foreach (var token in StreamAsync(prompt, options, cancellationToken))
            sb.Append(token);
        return sb.ToString();
    }

    public async IAsyncEnumerable<string> StreamAsync(
        string prompt,
        InferenceOptions? options = null,
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        if (_chat is null)
            throw new InvalidOperationException("Model is not loaded.");

        options ??= new InferenceOptions();
        await _gate.WaitAsync(cancellationToken);
        try
        {
            _chat.Settings.MaxTokens = options.MaxTokens;
            _chat.Settings.Temperature = options.Temperature;

            var messages = new List<ChatMessage>();
            if (!string.IsNullOrWhiteSpace(options.SystemPrompt))
                messages.Add(ChatMessage.FromSystem(options.SystemPrompt));
            messages.Add(ChatMessage.FromUser(prompt));

            await foreach (var chunk in _chat.CompleteChatStreamingAsync(messages, cancellationToken))
            {
                var text = chunk.Choices?.FirstOrDefault()?.Delta?.Content;
                if (!string.IsNullOrEmpty(text))
                    yield return text;
            }
        }
        finally
        {
            _gate.Release();
        }
    }
}
