using System.Diagnostics;
using System.Text.RegularExpressions;

namespace __APP__.Services;

public sealed record KnowledgeChunk(string Id, string Scope, string Document, string Section, string Text, string File);

public sealed record RetrievedChunk(KnowledgeChunk Chunk, double Score);

/// <summary>
/// Local retrieval over the hotel's own knowledge base (Assets\knowledge\{public,staff}\*.md).
/// Each "## " section is one passage. Ranking is BM25 with light stemming and a small
/// hotel synonym table, all in memory on this device. Nothing is embedded or indexed in the cloud.
/// Hotels update their knowledge by editing the Markdown files; no retraining needed.
/// </summary>
public sealed class KnowledgeBase
{
    private const double K1 = 1.4;
    private const double B = 0.75;

    private static readonly HashSet<string> StopWords = new(
        ("a an and are as at be but by can do for from get go going had has have how i if in into is it its " +
         "me my of on or our out so some that the their them then there they this to us was we what when where " +
         "which who will with would you your should could any about also just like want need please tell").Split(' '));

    // Guest and staff vocabulary mapped onto the words used in the hotel's documents.
    private static readonly Dictionary<string, string[]> Synonyms = new()
    {
        ["aircon"] = new[] { "air", "conditioning", "ac", "thermostat" },
        ["ac"] = new[] { "air", "conditioning", "thermostat" },
        ["hvac"] = new[] { "air", "conditioning", "heating" },
        ["cold"] = new[] { "heating", "thermostat", "radiator" },
        ["hot"] = new[] { "air", "conditioning", "thermostat" },
        ["nut"] = new[] { "nuts", "allergy", "allergen" },
        ["peanut"] = new[] { "nuts", "allergy", "allergen" },
        ["allergic"] = new[] { "allergy", "allergen" },
        ["rain"] = new[] { "rainy", "indoor" },
        ["rainy"] = new[] { "indoor", "rain" },
        ["wet"] = new[] { "rainy", "indoor" },
        ["kid"] = new[] { "children", "family" },
        ["kids"] = new[] { "children", "family" },
        ["child"] = new[] { "children", "family" },
        ["daughter"] = new[] { "children", "family" },
        ["son"] = new[] { "children", "family" },
        ["dinner"] = new[] { "restaurant", "eat" },
        ["lunch"] = new[] { "restaurant", "eat" },
        ["eat"] = new[] { "restaurant", "cafe" },
        ["food"] = new[] { "restaurant", "cafe", "eat" },
        ["bags"] = new[] { "luggage", "storage" },
        ["leave"] = new[] { "checkout", "departure" },
        ["leaving"] = new[] { "checkout", "departure", "luggage" },
        ["checkout"] = new[] { "check", "departure" },
        ["pool"] = new[] { "swim", "swimming" },
        ["swim"] = new[] { "pool", "swimming" },
        ["key"] = new[] { "card", "re-encode" },
        ["card"] = new[] { "key" },
        ["colleague"] = new[] { "privacy", "confirm", "staying" },
        ["staying"] = new[] { "privacy", "confirm" },
        ["upset"] = new[] { "complaint", "last" },
        ["angry"] = new[] { "upset", "last" },
        ["escalate"] = new[] { "escalation", "engineer" },
        ["escalating"] = new[] { "escalation", "engineer" },
        ["broken"] = new[] { "not", "working" },
        ["directions"] = new[] { "walking", "route", "bus" },
        ["there"] = new[] { "walking", "route" },
        ["wifi"] = new[] { "wi", "fi", "network" },
        ["morning"] = new[] { "open" },
        ["itinerary"] = new[] { "things", "indoor", "walking" },
        ["something"] = new[] { "things" },
        ["do"] = new[] { "things" },
        ["activity"] = new[] { "things" },
        ["activities"] = new[] { "things" },
        ["nearby"] = new[] { "walk" },
        ["get"] = new[] { "walking", "route", "bus" },
        ["how"] = new[] { "walking", "route" },
    };

    private readonly List<KnowledgeChunk> _chunks = new();
    private readonly List<Dictionary<string, int>> _termFreqs = new();
    private readonly List<int> _lengths = new();
    private readonly Dictionary<string, int> _docFreq = new();
    private double _avgLength;

    public int DocumentCount { get; private set; }
    public int PassageCount => _chunks.Count;
    public double IndexMs { get; private set; }
    public IReadOnlyList<KnowledgeChunk> Chunks => _chunks;

    public static KnowledgeBase Load() => Load(Path.Combine(AppContext.BaseDirectory, "Assets", "knowledge"));

    public static KnowledgeBase Load(string root)
    {
        var kb = new KnowledgeBase();
        var sw = Stopwatch.StartNew();
        try
        {
            if (Directory.Exists(root))
            {
                foreach (var file in Directory.EnumerateFiles(root, "*.md", SearchOption.AllDirectories).OrderBy(f => f))
                {
                    var scope = Path.GetFileName(Path.GetDirectoryName(file)) ?? "public";
                    kb.AddDocument(file, scope.ToLowerInvariant(), File.ReadAllText(file));
                    kb.DocumentCount++;
                }
            }
        }
        catch (Exception ex)
        {
            AppLog.Write($"Knowledge base load failed: {ex.Message}");
        }
        kb.BuildIndex();
        kb.IndexMs = sw.Elapsed.TotalMilliseconds;
        return kb;
    }

    private void AddDocument(string file, string scope, string markdown)
    {
        var title = Path.GetFileNameWithoutExtension(file);
        string? section = null;
        var body = new List<string>();

        void Flush()
        {
            var text = string.Join(" ", body).Trim();
            if (section is not null && text.Length > 0)
                _chunks.Add(new KnowledgeChunk($"{scope}/{Path.GetFileName(file)}#{_chunks.Count}", scope, title, section, text, Path.GetFileName(file)));
            body.Clear();
        }

        foreach (var raw in markdown.Split('\n'))
        {
            var line = raw.TrimEnd('\r');
            if (line.StartsWith("# "))
                title = line[2..].Trim();
            else if (line.StartsWith("## "))
            {
                Flush();
                section = line[3..].Trim();
            }
            else if (line.Trim().Length > 0)
                body.Add(line.Trim());
        }
        Flush();
    }

    private void BuildIndex()
    {
        foreach (var chunk in _chunks)
        {
            // The section heading is weighted twice: it is the best summary of the passage.
            var terms = Tokenise($"{chunk.Section} {chunk.Section} {chunk.Document} {chunk.Text}");
            var tf = new Dictionary<string, int>();
            foreach (var t in terms)
                tf[t] = tf.GetValueOrDefault(t) + 1;
            _termFreqs.Add(tf);
            _lengths.Add(terms.Count);
            foreach (var t in tf.Keys)
                _docFreq[t] = _docFreq.GetValueOrDefault(t) + 1;
        }
        _avgLength = _lengths.Count == 0 ? 1 : _lengths.Average();
    }

    public List<RetrievedChunk> Search(string query, IReadOnlyCollection<string> scopes, int top = 3, int maxChars = 3200, bool diversify = false, double minRelativeScore = 0.25)
    {
        var queryTerms = ExpandQuery(query);
        var n = _chunks.Count;
        var scored = new List<RetrievedChunk>();
        for (var i = 0; i < n; i++)
        {
            if (scopes.Count > 0 && !scopes.Contains(_chunks[i].Scope))
                continue;
            double score = 0;
            foreach (var (term, weight) in queryTerms)
            {
                if (!_termFreqs[i].TryGetValue(term, out var f))
                    continue;
                var df = _docFreq[term];
                var idf = Math.Log(1 + (n - df + 0.5) / (df + 0.5));
                score += weight * idf * (f * (K1 + 1)) / (f + K1 * (1 - B + B * _lengths[i] / _avgLength));
            }
            if (score > 0)
                scored.Add(new RetrievedChunk(_chunks[i], score));
        }

        var results = new List<RetrievedChunk>();
        var perDocument = new Dictionary<string, int>();
        var used = 0;
        var ranked = scored.OrderByDescending(s => s.Score).ToList();
        if (ranked.Count == 0)
            return results;
        // Drop weak tail matches so the small model is not distracted by noise.
        var floor = ranked[0].Score * minRelativeScore;
        ranked = ranked.Where(r => r.Score >= floor).ToList();

        // Diversify (guest questions): the best passage from each relevant document first,
        // so an itinerary sees places to go, places to eat and how to get there.
        var firsts = ranked.GroupBy(r => r.Chunk.File).Select(g => g.First()).ToList();
        var order = diversify ? firsts.Concat(ranked.Except(firsts)).ToList() : ranked;

        foreach (var hit in order)
        {
            if (results.Count >= top)
                break;
            if (perDocument.GetValueOrDefault(hit.Chunk.File) >= 3)
                continue;
            if (used + hit.Chunk.Text.Length > maxChars && results.Count > 0)
                continue;
            results.Add(hit);
            perDocument[hit.Chunk.File] = perDocument.GetValueOrDefault(hit.Chunk.File) + 1;
            used += hit.Chunk.Text.Length;
        }
        return results.OrderByDescending(r => r.Score).ToList();
    }

    private static List<(string term, double weight)> ExpandQuery(string query)
    {
        var weights = new Dictionary<string, double>();
        foreach (var raw in Words(query))
        {
            if (Synonyms.TryGetValue(raw, out var extra) || Synonyms.TryGetValue(Stem(raw), out extra))
                foreach (var e in extra)
                {
                    var s = Stem(e);
                    weights[s] = Math.Max(weights.GetValueOrDefault(s), 0.6);
                }
            if (StopWords.Contains(raw))
                continue;
            var stem = Stem(raw);
            weights[stem] = Math.Max(weights.GetValueOrDefault(stem), 1.0);
        }
        return weights.Select(kv => (kv.Key, kv.Value)).ToList();
    }

    private static List<string> Tokenise(string text) =>
        Words(text).Where(w => !StopWords.Contains(w)).Select(Stem).ToList();

    private static IEnumerable<string> Words(string text) =>
        Regex.Matches(text.ToLowerInvariant(), @"[a-z0-9]+").Select(m => m.Value).Where(w => w.Length > 1);

    /// <summary>Tiny suffix stripper: enough to match "allergies"/"allergy", "rooms"/"room", "swimming"/"swim".</summary>
    private static string Stem(string w)
    {
        if (w.Length > 5 && w.EndsWith("ies")) return w[..^3] + "y";
        if (w.Length > 6 && w.EndsWith("ming")) return w[..^4];
        if (w.Length > 5 && w.EndsWith("ing")) return w[..^3];
        if (w.Length > 4 && w.EndsWith("es") && !w.EndsWith("ses")) return w[..^1];
        if (w.Length > 3 && w.EndsWith('s') && !w.EndsWith("ss")) return w[..^1];
        return w;
    }
}
