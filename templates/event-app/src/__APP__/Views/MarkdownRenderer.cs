using System.Text.RegularExpressions;
using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Microsoft.UI.Xaml.Media;

namespace __APP__.Views;

/// <summary>Renders the small subset of Markdown that local models produce (headings, bullets, bold).</summary>
public static partial class MarkdownRenderer
{
    [GeneratedRegex(@"^\s*([-*•]|\d+[\.\)])\s+(.*)$")]
    private static partial Regex BulletRegex();

    [GeneratedRegex(@"^\s*#{1,6}\s*(.*)$")]
    private static partial Regex HeadingRegex();

    [GeneratedRegex(@"^\s*\**([A-Z][A-Za-z0-9 &/'\(\)\-]{1,42}?)\**:\**\s*(.*)$")]
    private static partial Regex LabelRegex();

    public static void Render(RichTextBlock target, string markdown, Brush accent)
    {
        target.Blocks.Clear();
        foreach (var raw in markdown.Replace("\r", "").Split('\n'))
        {
            var line = raw.TrimEnd();
            if (line.Trim().Length == 0 || line.Trim() is "---" or "***")
                continue;

            var paragraph = new Paragraph { Margin = new Thickness(0, 0, 0, 5) };
            var heading = HeadingRegex().Match(line);
            var bullet = BulletRegex().Match(line);
            var label = LabelRegex().Match(line);
            var trimmed = line.Trim();
            var boldLine = trimmed.StartsWith("**") && trimmed.EndsWith("**") && trimmed.Length > 4 && trimmed.IndexOf("**", 2) == trimmed.Length - 2;
            var labelHeading = !bullet.Success && label.Success && label.Groups[2].Value.Trim().Length == 0;

            if (heading.Success || boldLine || labelHeading)
            {
                var text = heading.Success ? heading.Groups[1].Value : labelHeading ? label.Groups[1].Value : trimmed.Trim('*');
                paragraph.Margin = new Thickness(0, 10, 0, 4);
                paragraph.Inlines.Add(new Run
                {
                    Text = text.Replace("**", "").Trim(),
                    FontWeight = FontWeights.SemiBold,
                    FontSize = 15,
                    Foreground = accent
                });
            }
            else if (bullet.Success)
            {
                var marker = char.IsDigit(bullet.Groups[1].Value[0]) ? bullet.Groups[1].Value + " " : "•  ";
                paragraph.Margin = new Thickness(14, 0, 0, 4);
                paragraph.TextIndent = -14;
                paragraph.Inlines.Add(new Run { Text = marker, Foreground = accent });
                AddInline(paragraph, bullet.Groups[2].Value);
            }
            else if (label.Success)
            {
                paragraph.Inlines.Add(new Run { Text = label.Groups[1].Value.Trim() + ": ", FontWeight = FontWeights.SemiBold, Foreground = accent });
                AddInline(paragraph, label.Groups[2].Value);
            }
            else
            {
                AddInline(paragraph, line);
            }
            target.Blocks.Add(paragraph);
        }
    }

    private static void AddInline(Paragraph paragraph, string text)
    {
        var parts = text.Split("**");
        for (var i = 0; i < parts.Length; i++)
        {
            if (parts[i].Length == 0)
                continue;
            var run = new Run { Text = parts[i] };
            if (i % 2 == 1)
                run.FontWeight = FontWeights.SemiBold;
            paragraph.Inlines.Add(run);
        }
    }
}
