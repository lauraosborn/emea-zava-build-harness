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

public sealed class ThemeConfig
{
    public string Mode { get; set; } = "light";
    public string Canvas { get; set; } = "#F6F7F9";
    public string Surface { get; set; } = "#FFFFFF";
    public string Surface2 { get; set; } = "#EEF0F4";
    public string Stroke { get; set; } = "#DFE3EA";
    public string Ink { get; set; } = "#171B22";
    public string Ink2 { get; set; } = "#4A5160";
    public string Muted { get; set; } = "#8A8F98";
    public string Accent { get; set; } = "#2F5BD3";
    public string AccentInk { get; set; } = "#FFFFFF";
    public string AccentSoft { get; set; } = "#E5ECFB";
    public string DisplayFont { get; set; } = "Segoe UI Variable Display";
    public int Corner { get; set; } = 14;
}

public sealed class LocalDriver
{
    public string Id { get; set; } = "";
    public string Title { get; set; } = "";
    public string Detail { get; set; } = "";
}

public sealed class BrandConfig
{
    public string Domain { get; set; } = "Enterprise workspace";
    public string MarkGlyph { get; set; } = "\uE946";
    public string WorkflowLabel { get; set; } = "Workflow";
    public string HeroMetric1 { get; set; } = "Private context";
    public string HeroMetric2 { get; set; } = "On-device answers";
    public string HeroMetric3 { get; set; } = "Offline-ready";
}

public sealed class PrototypeConfig
{
    public bool EnablesImageInput { get; set; }
    public string DemoImage { get; set; } = "";
    public string DemoImageLabel { get; set; } = "";
    public string DemoImagePrompt { get; set; } = "";
    public string UploadPromptTemplate { get; set; } = "Uploaded file: {fileName}. Analyse it.";
}

/// <summary>
/// The app's configuration, written by the Zava Live Build harness into Assets\app-config.json:
/// what the app does and for whom (industry theme, modes, context fields, why it runs locally),
/// plus the pinned on-device model and backup. Pinning the exact model build means the demo
/// cannot change underneath the presenter.
/// </summary>
public sealed class AppConfig
{
    public string AppTitle { get; set; } = "__APP_TITLE__";
    public string Tagline { get; set; } = "";
    public string Idea { get; set; } = "";
    public string Industry { get; set; } = "general";
    public string Organisation { get; set; } = "Zava";
    public ThemeConfig Theme { get; set; } = new();
    public List<ModeConfig> Modes { get; set; } = new();
    public string ContextTitle { get; set; } = "";
    public List<ContextFieldConfig> ContextFields { get; set; } = new();
    public List<LocalDriver> LocalDrivers { get; set; } = new();
    public BrandConfig Brand { get; set; } = new();
    public PrototypeConfig Prototype { get; set; } = new();
    public List<string> Languages { get; set; } = new();
    public string SystemPrompt { get; set; } = "You are a helpful assistant running on this device.";
    public string InputLabel { get; set; } = "What would you like help with?";
    public List<string> SamplePrompts { get; set; } = new();
    public ModelRef Primary { get; set; } = new() { Alias = "phi-4-mini" };
    public ModelRef? Backup { get; set; }
    public List<string> SelectionReasons { get; set; } = new();
    public int FirstTokenTimeoutSeconds { get; set; } = 45;
    public int MaxTokens { get; set; } = 400;
    public float Temperature { get; set; } = 0.3f;

    /// <summary>The configured modes, or one mode built from the top-level prompt and samples.</summary>
    public List<ModeConfig> EffectiveModes => Modes.Count > 0
        ? Modes
        : new() { new ModeConfig { Name = AppTitle, InputLabel = InputLabel, SystemPrompt = SystemPrompt, SamplePrompts = SamplePrompts } };

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
