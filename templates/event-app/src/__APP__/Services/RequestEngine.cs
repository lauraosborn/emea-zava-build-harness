using System.Text;

namespace __APP__.Services;

/// <summary>One job the app does (for example "Guest concierge" or "Staff assistant"). From app-config.json.</summary>
public sealed class ModeConfig
{
    public string Id { get; set; } = "main";
    public string Name { get; set; } = "Assistant";
    public string InputLabel { get; set; } = "What would you like help with?";
    public string Action { get; set; } = "Go";
    public string OutputTitle { get; set; } = "Result";
    public string SystemPrompt { get; set; } = "";
    public List<string> Scopes { get; set; } = new() { "public" };
    public bool UsesContext { get; set; }
    public bool UsesLanguage { get; set; }
    public string DefaultLanguage { get; set; } = "Spanish";
    public int TopPassages { get; set; } = 4;
    public bool Diversify { get; set; }
    /// <summary>Passages scoring below this fraction of the best match are dropped as noise.</summary>
    public double MinRelativeScore { get; set; } = 0.25;
    public List<string> SamplePrompts { get; set; } = new();
}

/// <summary>A private detail typed into the context panel (for example "Allergies"). From app-config.json.</summary>
public sealed class ContextFieldConfig
{
    public string Key { get; set; } = "";
    public string Label { get; set; } = "";
    public string Placeholder { get; set; } = "";
}

public sealed record AppRequest(
    ModeConfig Mode,
    string Question,
    string SystemPrompt,
    string ModelPrompt,
    string ReplayKey,
    IReadOnlyList<RetrievedChunk> Sources);

/// <summary>
/// Builds every request on this device: retrieve the organisation's own knowledge (local RAG),
/// add the private context the user typed, and ground the prompt. Context is held in memory
/// only and is never logged, saved or sent.
/// </summary>
public sealed class RequestEngine
{
    public KnowledgeBase Knowledge { get; } = KnowledgeBase.Load();

    public AppRequest Build(ModeConfig mode, string question, IReadOnlyDictionary<string, string>? context,
                            IReadOnlyList<ContextFieldConfig> fields, string? language, DateTime now)
    {
        question = question.Trim();
        var lang = string.IsNullOrWhiteSpace(language) ? mode.DefaultLanguage : language!;
        var filled = mode.UsesContext && context is not null
            ? fields.Where(f => !string.IsNullOrWhiteSpace(context.GetValueOrDefault(f.Key)))
                    .Select(f => (f.Label, Value: context[f.Key].Trim())).ToList()
            : new List<(string Label, string Value)>();

        var sources = new List<RetrievedChunk>();
        if (Knowledge.PassageCount > 0 && mode.TopPassages > 0)
        {
            // Private context also steers retrieval (for example an allergy finds suitable venues). All local.
            var searchText = question + " " + string.Join(" ", filled.Select(f => f.Value));
            sources = Knowledge.Search(searchText, mode.Scopes, mode.TopPassages,
                diversify: mode.Diversify, minRelativeScore: mode.MinRelativeScore);
        }

        var system = new StringBuilder(mode.SystemPrompt.Replace("{language}", lang));
        if (sources.Count > 0)
            system.Append(" Use ONLY facts from the knowledge provided; never invent names, prices, times, steps or contact details. If it is not covered, say a colleague will check.");
        else if (Knowledge.PassageCount > 0 && mode.TopPassages > 0)
            system.Append(" Nothing in the knowledge base covers this request: say so briefly and suggest asking a colleague.");
        system.Append(" Never claim to browse the internet or call cloud services.");

        var sb = new StringBuilder();
        sb.AppendLine($"Today is {now:dddd d MMMM yyyy}.");
        if (filled.Count > 0)
        {
            sb.AppendLine("Private context (held on this device):");
            foreach (var (label, value) in filled)
                sb.AppendLine($"- {label}: {value}");
        }
        if (sources.Count > 0)
        {
            sb.AppendLine("Knowledge:");
            var i = 1;
            // The document title tells a small model what kind of fact this is (a place to eat, a procedure...).
            foreach (var s in sources)
                sb.AppendLine($"[{i++}] {s.Chunk.Document} > {s.Chunk.Section}: {s.Chunk.Text}");
        }
        sb.AppendLine();
        sb.AppendLine(mode.UsesLanguage ? $"Request (reply in {lang}): {question}" : $"Request: {question}");

        var key = mode.UsesLanguage ? $"[{mode.Name} {lang}] {question}" : $"[{mode.Name}] {question}";
        return new AppRequest(mode, question, system.ToString(), sb.ToString(), key, sources);
    }
}
