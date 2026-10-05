using System.Text.Json;

namespace __APP__.Services;

public sealed class ModelRef
{
    public string Alias { get; set; } = "";
    public string ModelId { get; set; } = "";
    public string ExecutionProvider { get; set; } = "";
    public int SizeMb { get; set; }
    public string License { get; set; } = "";
}

/// <summary>
/// The app's stage configuration, written by the Zava Live Build harness into
/// Assets\app-config.json: what the app does, the pinned NPU model, the backup
/// model, and why they were chosen. Pinning the exact model build (including
/// version) means the demo cannot change underneath the presenter.
/// </summary>
public sealed class AppConfig
{
    public string AppTitle { get; set; } = "__APP_TITLE__";
    public string Tagline { get; set; } = "";
    public string Idea { get; set; } = "";
    public string SystemPrompt { get; set; } = "You are a helpful assistant running on this device's NPU.";
    public string InputLabel { get; set; } = "What would you like help with?";
    public List<string> SamplePrompts { get; set; } = new();
    public ModelRef Primary { get; set; } = new() { Alias = "phi-4-mini" };
    public ModelRef? Backup { get; set; }
    public List<string> SelectionReasons { get; set; } = new();
    public int FirstTokenTimeoutSeconds { get; set; } = 45;
    public int MaxTokens { get; set; } = 400;
    public float Temperature { get; set; } = 0.3f;

    private static readonly JsonSerializerOptions Options = new() { PropertyNameCaseInsensitive = true };

    public static AppConfig Load()
    {
        try
        {
            var path = Path.Combine(AppContext.BaseDirectory, "Assets", "app-config.json");
            if (File.Exists(path))
                return JsonSerializer.Deserialize<AppConfig>(File.ReadAllText(path), Options) ?? new AppConfig();
        }
        catch (Exception ex)
        {
            AppLog.Write($"app-config.json could not be read: {ex.Message}");
        }
        return new AppConfig();
    }
}
