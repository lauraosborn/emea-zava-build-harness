using __APP__.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;

namespace __APP__;

public partial class App : Application
{
    public static MainWindow? MainWindow { get; private set; }

    public App()
    {
        // The industry theme decides light or dark; this can only be set in the constructor.
        RequestedTheme = LocalAiRuntime.Instance.Config.Theme.Mode.Equals("dark", StringComparison.OrdinalIgnoreCase)
            ? ApplicationTheme.Dark
            : ApplicationTheme.Light;
        InitializeComponent();
        UnhandledException += (_, e) =>
        {
            // A live demo must never crash in front of an audience.
            AppLog.Write($"Unhandled UI exception: {e.Exception}");
            e.Handled = true;
        };
    }

    protected override async void OnLaunched(LaunchActivatedEventArgs args)
    {
        var cli = Environment.GetCommandLineArgs();
        if (cli.Any(a => a.Equals("--kb-check", StringComparison.OrdinalIgnoreCase)))
        {
            Environment.Exit(WarmupRunner.RunKnowledgeCheck());
            return;
        }

        if (cli.Any(a => a.Equals("--warmup", StringComparison.OrdinalIgnoreCase)))
        {
            // Headless preflight: compile the pinned model into the shared cache and rehearse
            // each mode, then exit. Run by the harness before the show.
            var code = await WarmupRunner.RunAsync(cli);
            Environment.Exit(code);
            return;
        }

        ApplyTheme(LocalAiRuntime.Instance.Config.Theme);
        MainWindow = new MainWindow();
        MainWindow.Activate();
        _ = LocalAiRuntime.Instance.InitializeAsync();
    }

    /// <summary>
    /// Recolour the palette in place with the industry theme before the window loads. The brushes
    /// are mutated rather than replaced, so styles that already captured them follow the brand too.
    /// </summary>
    private void ApplyTheme(ThemeConfig theme)
    {
        void Brush(string key, string hex)
        {
            try
            {
                if (Resources[key] is SolidColorBrush brush)
                    brush.Color = ThemeColors.Parse(hex);
            }
            catch (Exception ex) { AppLog.Write($"Theme colour {key}={hex} ignored: {ex.Message}"); }
        }

        Brush("CanvasBrush", theme.Canvas);
        Brush("PanelBrush", theme.Surface);
        Brush("Panel2Brush", theme.Surface2);
        Brush("StrokeBrush", theme.Stroke);
        Brush("InkBrush", theme.Ink);
        Brush("InkSecondaryBrush", theme.Ink2);
        Brush("InkMutedBrush", theme.Muted);
        Brush("AccentBrush", theme.Accent);
        Brush("AccentInkBrush", theme.AccentInk);
        Brush("AccentSoftBrush", theme.AccentSoft);
        if (theme.Mode.Equals("dark", StringComparison.OrdinalIgnoreCase))
        {
            Brush("NpuBrush", "#4ADE80");
            Brush("AmberBrush", "#F2C14E");
        }

        try
        {
            Resources["DisplayFont"] = new FontFamily(theme.DisplayFont);
            Resources["Card"] = CardStyle(theme.Corner, 22, "PanelBrush");
            Resources["InsetCard"] = CardStyle(Math.Max(6, theme.Corner - 4), 14, "Panel2Brush");
            if (Resources["Heading"] is Style heading)
            {
                var h = new Style(typeof(Microsoft.UI.Xaml.Controls.TextBlock)) { BasedOn = heading };
                h.Setters.Add(new Setter(Microsoft.UI.Xaml.Controls.TextBlock.FontFamilyProperty, Resources["DisplayFont"]));
                Resources["Heading"] = h;
            }
        }
        catch (Exception ex)
        {
            AppLog.Write($"Theme shape ignored: {ex.Message}");
        }
    }

    private Style CardStyle(int corner, int padding, string background)
    {
        var style = new Style(typeof(Microsoft.UI.Xaml.Controls.Border));
        style.Setters.Add(new Setter(Microsoft.UI.Xaml.Controls.Border.BackgroundProperty, Resources[background]));
        style.Setters.Add(new Setter(Microsoft.UI.Xaml.Controls.Border.BorderBrushProperty, Resources["StrokeBrush"]));
        style.Setters.Add(new Setter(Microsoft.UI.Xaml.Controls.Border.BorderThicknessProperty, new Thickness(1)));
        style.Setters.Add(new Setter(Microsoft.UI.Xaml.Controls.Border.CornerRadiusProperty, new CornerRadius(corner)));
        style.Setters.Add(new Setter(Microsoft.UI.Xaml.Controls.Border.PaddingProperty, new Thickness(padding)));
        return style;
    }
}

public static class ThemeColors
{
    public static Windows.UI.Color Parse(string hex)
    {
        hex = hex.Trim().TrimStart('#');
        if (hex.Length == 6)
            hex = "FF" + hex;
        var v = Convert.ToUInt32(hex, 16);
        return Windows.UI.Color.FromArgb((byte)(v >> 24), (byte)(v >> 16), (byte)(v >> 8), (byte)v);
    }

    public static Windows.UI.Color WithAlpha(Windows.UI.Color c, byte a) => Windows.UI.Color.FromArgb(a, c.R, c.G, c.B);
}
