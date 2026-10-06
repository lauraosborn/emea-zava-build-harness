using System.Text.RegularExpressions;

namespace __APP__.Services;

/// <summary>
/// Light clean-up of a finished answer from a small local model: drops empty sections such as
/// "**Escalate:** N/A" or "None stated". Never changes the facts in the answer.
/// </summary>
public static class AnswerCleaner
{
    private static readonly Regex EmptyLine = new(
        @"^\s*(?:[-*]\s*)?(?:\*\*)?[A-Za-z ]{2,30}:?(?:\*\*)?:?\s*(?:N/?A|None|None stated|Not applicable|No escalation[^\n]*|Nothing to (?:add|report|escalate)[^\n]*)\s*(?:\(.*\))?\.?\s*$",
        RegexOptions.IgnoreCase | RegexOptions.Multiline);

    // A tip tacked onto the end of a list line ("... nut-free cafe. - Tip: book ahead"): small models
    // invent these; the single standalone "Tip:" line at the end is kept.
    private static readonly Regex InlineTip = new(@"[ \t]+[-–][ \t]+(?:\*\*)?Tip(?:\*\*)?:[^\n]*", RegexOptions.IgnoreCase);

    // Template placeholders copied literally by small models: "<Morning>:" becomes "Morning:".
    private static readonly Regex BracketLabel = new(@"^(\s*(?:\*\*)?)<([A-Za-z ]{2,24})>", RegexOptions.Multiline);

    public static string Clean(string text)
    {
        if (string.IsNullOrWhiteSpace(text))
            return text;
        var cleaned = EmptyLine.Replace(text, "");
        cleaned = InlineTip.Replace(cleaned, "");
        cleaned = BracketLabel.Replace(cleaned, "$1$2");
        cleaned = Regex.Replace(cleaned, @"\n{3,}", "\n\n");
        return cleaned.Trim();
    }
}
