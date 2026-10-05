using System.Text.Json;

namespace __APP__.Services;

/// <summary>
/// All app state lives under %LOCALAPPDATA%\__APP__. Nothing is written to a
/// network location and nothing is uploaded.
/// </summary>
public static class AppPaths
{
    public static string Root { get; } = Ensure(Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "__APP__"));

    public static string Models { get; } = Ensure(Path.Combine(Root, "models"));
    public static string OpenVinoCache { get; } = Ensure(Path.Combine(Root, "ov-cache"));
    public static string Telemetry { get; } = Ensure(Path.Combine(Root, "telemetry"));
    public static string Logs { get; } = Ensure(Path.Combine(Root, "logs"));
    public static string Replay { get; } = Path.Combine(Root, "replay.json");
    public static string WarmupReport { get; } = Path.Combine(Root, "warmup.json");

    /// <summary>Model cache used by the Foundry Local CLI. Reused via hard links so nothing is downloaded twice.</summary>
    public static string FoundryCliCache { get; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".foundry", "cache", "models");

    private static string Ensure(string path)
    {
        Directory.CreateDirectory(path);
        return path;
    }
}

public static class AppLog
{
    private static readonly object Gate = new();

    public static void Write(string message)
    {
        try
        {
            lock (Gate)
            {
                File.AppendAllText(Path.Combine(AppPaths.Logs, $"app_{DateTime.Now:yyyyMMdd}.log"),
                    $"{DateTime.Now:HH:mm:ss.fff} {message}{Environment.NewLine}");
            }
        }
        catch
        {
            // Logging must never break the demo.
        }
    }
}

public sealed class AppSettings
{
    /// <summary>Model IDs (including version) whose NPU-compiled blob is already in the OpenVINO cache.</summary>
    public List<string> NpuCompiledModels { get; set; } = new();

    private static readonly string FilePath = Path.Combine(AppPaths.Root, "settings.json");
    private static AppSettings? _current;

    public static AppSettings Current => _current ??= Load();

    private static AppSettings Load()
    {
        try
        {
            if (File.Exists(FilePath))
                return JsonSerializer.Deserialize<AppSettings>(File.ReadAllText(FilePath)) ?? new AppSettings();
        }
        catch (Exception ex)
        {
            AppLog.Write($"Settings load failed: {ex.Message}");
        }
        return new AppSettings();
    }

    public void Save()
    {
        try
        {
            File.WriteAllText(FilePath, JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));
        }
        catch (Exception ex)
        {
            AppLog.Write($"Settings save failed: {ex.Message}");
        }
    }
}
