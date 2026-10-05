using System.Text.Json;
using System.Text.RegularExpressions;

namespace __APP__.Services;

public sealed record ReplayEntry(
    string Prompt,
    string Response,
    string ModelId,
    string Device,
    DateTime RecordedAt,
    double TokensPerSecond);

/// <summary>
/// Recorded outputs from this app's own on-device model, captured during the
/// preflight warm-up. Used only as a clearly labelled safety net: if the live
/// model cannot answer, the audience sees a REPLAY badge, never a fake "live" run.
/// </summary>
public sealed class ReplayLibrary
{
    private static readonly JsonSerializerOptions Options = new() { PropertyNameCaseInsensitive = true, WriteIndented = true };

    public List<ReplayEntry> Entries { get; private set; } = new();

    public static ReplayLibrary Load()
    {
        var library = new ReplayLibrary();
        foreach (var path in new[] { AppPaths.Replay, Path.Combine(AppContext.BaseDirectory, "Assets", "replay.json") })
        {
            try
            {
                if (!File.Exists(path))
                    continue;
                var entries = JsonSerializer.Deserialize<List<ReplayEntry>>(File.ReadAllText(path), Options);
                if (entries is { Count: > 0 })
                {
                    library.Entries = entries;
                    break;
                }
            }
            catch (Exception ex)
            {
                AppLog.Write($"Replay file {path} unreadable: {ex.Message}");
            }
        }
        return library;
    }

    public void Save(IEnumerable<ReplayEntry> entries)
    {
        Entries = entries.ToList();
        File.WriteAllText(AppPaths.Replay, JsonSerializer.Serialize(Entries, Options));
    }

    /// <summary>Exact match first, then the closest recorded prompt by word overlap.</summary>
    public ReplayEntry? Find(string prompt)
    {
        if (Entries.Count == 0)
            return null;

        var key = Normalise(prompt);
        var exact = Entries.FirstOrDefault(e => Normalise(e.Prompt) == key);
        if (exact is not null)
            return exact;

        var words = Words(prompt);
        var best = Entries
            .Select(e => (entry: e, score: Jaccard(words, Words(e.Prompt))))
            .OrderByDescending(x => x.score)
            .First();
        return best.score >= 0.35 ? best.entry : null;
    }

    private static string Normalise(string s) => Regex.Replace(s.Trim().ToLowerInvariant(), @"\s+", " ");

    private static HashSet<string> Words(string s) =>
        Regex.Matches(s.ToLowerInvariant(), @"[a-z0-9]{3,}").Select(m => m.Value).ToHashSet();

    private static double Jaccard(HashSet<string> a, HashSet<string> b)
    {
        if (a.Count == 0 || b.Count == 0)
            return 0;
        return (double)a.Intersect(b).Count() / a.Union(b).Count();
    }
}
