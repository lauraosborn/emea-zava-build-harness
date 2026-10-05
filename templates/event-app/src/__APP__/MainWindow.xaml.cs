using System.Text;
using __APP__.Branding;
using __APP__.Services;
using __APP__.Views;
using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.Foundation;
using Windows.Networking.Connectivity;

namespace __APP__;

public sealed partial class MainWindow : Window
{
    private const int SparkPoints = 60;

    private readonly LocalAiRuntime _runtime = LocalAiRuntime.Instance;
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromMilliseconds(500) };
    private readonly Queue<double> _npuHistory = new(Enumerable.Repeat(0.0, SparkPoints));
    private CancellationTokenSource? _cts;
    private bool _sampling;
    private double _npuNow;
    private DateTime _lastRender = DateTime.MinValue;

    public MainWindow()
    {
        InitializeComponent();
        ConfigureWindow();
        ApplyConfig();

        _runtime.Consent.ConsentPrompt = AskGpuConsentAsync;
        _runtime.Changed += () => DispatcherQueue.TryEnqueue(UpdateStatus);
        NetworkInformation.NetworkStatusChanged += _ => DispatcherQueue.TryEnqueue(UpdateNetwork);

        _timer.Tick += async (_, _) => await TickAsync();
        _timer.Start();
        Closed += (_, _) => _timer.Stop();

        // Presenter shortcuts only fire when focus is inside the window, so keep the
        // prompt box focused at start-up, on reactivation and after every run.
        RootGrid.Loaded += (_, _) => FocusPrompt();
        Activated += (_, e) =>
        {
            if (e.WindowActivationState != WindowActivationState.Deactivated)
                FocusPrompt();
        };

        UpdateNetwork();
        UpdateStatus();
        _keyHook = new PresenterKeyHook(this, HandlePresenterKey);
    }

    private readonly PresenterKeyHook _keyHook;

    /// <summary>Shared by the XAML accelerators and the window-level backstop hook.</summary>
    private bool HandlePresenterKey(Windows.System.VirtualKey key, bool ctrl)
    {
        switch (key)
        {
            case Windows.System.VirtualKey.F5:
            case Windows.System.VirtualKey.Enter when ctrl:
                _ = RunAsync();
                return true;
            case Windows.System.VirtualKey.Escape:
                _cts?.Cancel();
                return true;
            case Windows.System.VirtualKey.F11:
                ToggleFullScreen();
                return true;
            case Windows.System.VirtualKey.R when ctrl:
                ReplaySwitch.IsOn = !ReplaySwitch.IsOn;
                return true;
            case >= Windows.System.VirtualKey.Number1 and <= Windows.System.VirtualKey.Number3 when ctrl:
                var index = key - Windows.System.VirtualKey.Number1;
                if (index < _runtime.Config.SamplePrompts.Count)
                    PromptBox.Text = _runtime.Config.SamplePrompts[index];
                FocusPrompt();
                return true;
            default:
                return false;
        }
    }

    private void ToggleFullScreen() =>
        AppWindow.SetPresenter(AppWindow.Presenter.Kind == AppWindowPresenterKind.FullScreen
            ? AppWindowPresenterKind.Overlapped
            : AppWindowPresenterKind.FullScreen);

    private void FocusPrompt() =>
        DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Low, () =>
        {
            if (PromptBox.XamlRoot is not null)
                PromptBox.Focus(FocusState.Programmatic);
        });

    private void ConfigureWindow()
    {
        Title = _runtime.Config.AppTitle;
        WatermarkText.Text = BrandingConstants.Watermark;
        try
        {
            AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "AppIcon.ico"));
            if (AppWindow.TitleBar is { } bar)
            {
                var dark = Windows.UI.Color.FromArgb(255, 7, 9, 13);
                bar.BackgroundColor = dark;
                bar.ButtonBackgroundColor = dark;
                bar.InactiveBackgroundColor = dark;
                bar.ButtonInactiveBackgroundColor = dark;
                bar.ForegroundColor = Colors.White;
                bar.ButtonForegroundColor = Colors.White;
            }
            if (AppWindow.Presenter is OverlappedPresenter presenter)
                presenter.Maximize();
        }
        catch (Exception ex)
        {
            AppLog.Write($"Window configuration failed: {ex.Message}");
        }
    }

    private void ApplyConfig()
    {
        var config = _runtime.Config;
        AppTitleText.Text = config.AppTitle;
        TaglineText.Text = config.Tagline;
        InputLabelText.Text = config.InputLabel;

        for (var i = 0; i < config.SamplePrompts.Count && i < 3; i++)
        {
            var prompt = config.SamplePrompts[i];
            var button = new Button
            {
                HorizontalAlignment = HorizontalAlignment.Stretch,
                HorizontalContentAlignment = HorizontalAlignment.Left,
                Padding = new Thickness(12, 7, 12, 7),
                Content = new TextBlock
                {
                    Text = $"Ctrl+{i + 1}   {prompt}",
                    TextTrimming = TextTrimming.CharacterEllipsis,
                    FontSize = 13
                }
            };
            ToolTipService.SetToolTip(button, prompt);
            button.Click += (_, _) =>
            {
                PromptBox.Text = prompt;
                FocusPrompt();
            };
            SamplesPanel.Children.Add(button);
        }
        if (config.SamplePrompts.Count > 0)
            PromptBox.Text = config.SamplePrompts[0];

        foreach (var reason in config.SelectionReasons)
            ReasonsPanel.Children.Add(new TextBlock { Text = "· " + reason, Style = (Style)Application.Current.Resources["Body"], FontSize = 13 });
        if (config.SelectionReasons.Count == 0)
            ReasonsPanel.Children.Add(new TextBlock { Text = "Selected by the Zava Live Build harness.", Style = (Style)Application.Current.Resources["Body"] });
    }

    // ── Live telemetry ────────────────────────────────────────────────────

    private async Task TickAsync()
    {
        if (_sampling)
            return;
        _sampling = true;
        try
        {
            _npuNow = await Task.Run(_runtime.NpuMonitor.Sample);
        }
        finally
        {
            _sampling = false;
        }

        _npuHistory.Dequeue();
        _npuHistory.Enqueue(_npuNow);
        DrawSparkline();
        NpuStatusText.Text = _runtime.NpuMonitor.Available ? $"NPU: {_npuNow:F0}%" : "NPU: counters unavailable";
        NpuPeakText.Text = $"PEAK {_runtime.NpuMonitor.PeakPercent:F0}%";
        UpdateTransparencyLine();
    }

    private void DrawSparkline()
    {
        var width = Math.Max(10, SparkHost.ActualWidth);
        var height = Math.Max(10, SparkHost.ActualHeight);
        var points = new PointCollection();
        var i = 0;
        foreach (var value in _npuHistory)
        {
            var x = i++ * width / (SparkPoints - 1);
            var y = height - 4 - (Math.Clamp(value, 0, 100) / 100.0 * (height - 8));
            points.Add(new Point(x, y));
        }
        NpuSparkline.Points = points;
    }

    private void UpdateStatus()
    {
        var rt = _runtime;
        ModelNameText.Text = $"{BrandingConstants.ModelInUseLabel} {rt.ModelAlias}{(rt.UsingBackup ? " (backup)" : "")}";
        ModelIdText.Text = rt.ModelId;
        var ep = string.IsNullOrEmpty(rt.ExecutionProvider) ? "" : rt.ExecutionProvider.Replace("ExecutionProvider", "") + " EP  ·  ";
        var size = rt.Config.Primary.SizeMb > 0 && !rt.UsingBackup ? $"{rt.Config.Primary.SizeMb / 1024.0:F2} GB  ·  " : "";
        var license = rt.UsingBackup ? rt.Config.Backup?.License : rt.Config.Primary.License;
        ModelMetaText.Text = $"{ep}{size}{license}";
        LocalAiStatusText.Text = $"{BrandingConstants.LocalAiStatusLabel} {rt.Status}";
        LoadProgress.Visibility = rt.State is RuntimeState.Starting or RuntimeState.Preparing or RuntimeState.Downloading
            ? Visibility.Visible : Visibility.Collapsed;
        LoadTimeText.Text = rt.State == RuntimeState.Ready ? $"{rt.LoadMs / 1000:F1}s" : "-";

        NpuNameText.Text = $"NPU  {(rt.Telemetry.NpuDetected ? rt.Telemetry.NpuName : "not detected")}";
        CpuNameText.Text = $"CPU  {rt.Telemetry.CpuName}";

        var (label, brush) = rt.ForceReplay
            ? ("REPLAY", "AmberBrush")
            : rt.State switch
            {
                RuntimeState.Ready when rt.Device == "NPU" => ("NPU", "NpuBrush"),
                RuntimeState.Ready => (rt.Device.ToUpperInvariant(), "BlueBrush"),
                RuntimeState.Replay => ("REPLAY", "AmberBrush"),
                _ => ("LOADING", "InkMutedBrush")
            };
        DeviceBadgeText.Text = label;
        DeviceBadgeText.Foreground = (Brush)Application.Current.Resources[brush];
        DeviceChipText.Text = label == "NPU" ? "LIVE ON NPU" : label;
        DeviceChipText.Foreground = DeviceBadgeText.Foreground;

        HistoryPanel.Children.Clear();
        foreach (var r in rt.History)
        {
            var where = r.Mode switch { InferenceMode.LiveNpu => "NPU", InferenceMode.LiveGpu => "GPU", _ => "REPLAY" };
            HistoryPanel.Children.Add(new TextBlock
            {
                Style = (Style)Application.Current.Resources["Mono"],
                FontSize = 12,
                Text = $"{r.At:HH:mm:ss}  {where,-6} {r.Model,-14} {r.Tokens,4} tok  {r.TokensPerSecond,5:F1} tok/s  TTFT {r.FirstTokenMs / 1000,4:F1}s  NPU peak {r.PeakNpuPercent:F0}%"
            });
        }
        if (rt.History.Count == 0)
            HistoryPanel.Children.Add(new TextBlock { Text = "No runs yet.", Style = (Style)Application.Current.Resources["Body"] });

        UpdateTransparencyLine();
    }

    private void UpdateTransparencyLine()
    {
        TransparencyText.Text = BrandingConstants.FormatTransparencyLine(
            _runtime.ModelId, _runtime.State == RuntimeState.Ready, _npuNow, _runtime.LastTokensPerSecond);
    }

    private void UpdateNetwork()
    {
        try
        {
            var profile = NetworkInformation.GetInternetConnectionProfile();
            var online = profile?.GetNetworkConnectivityLevel() == NetworkConnectivityLevel.InternetAccess;
            NetworkText.Text = online ? "ONLINE  ·  AI RUNS LOCALLY" : "OFFLINE  ·  AI STILL RUNNING";
            NetworkText.Foreground = (Brush)Application.Current.Resources[online ? "InkSecondaryBrush" : "NpuBrush"];
        }
        catch
        {
            NetworkText.Text = "NETWORK: UNKNOWN";
        }
    }

    // ── Run / stop ────────────────────────────────────────────────────────

    private async Task RunAsync()
    {
        if (_cts is not null)
            return;
        var prompt = PromptBox.Text.Trim();
        if (prompt.Length == 0)
            return;

        _cts = new CancellationTokenSource();
        RunButton.IsEnabled = false;
        StopButton.IsEnabled = true;
        OutputNoteText.Visibility = Visibility.Collapsed;
        OutputText.Blocks.Clear();
        var accent = (Brush)Application.Current.Resources["NpuBrush"];
        var text = new StringBuilder();
        GenerationUpdate? last = null;
        InferenceMode? shownMode = null;

        try
        {
            await foreach (var update in _runtime.GenerateAsync(prompt, _cts.Token))
            {
                if (shownMode != update.Mode)
                {
                    if (shownMode is not null)
                        text.Clear();
                    shownMode = update.Mode;
                    ShowMode(update);
                    accent = (Brush)Application.Current.Resources[update.Mode == InferenceMode.Replay ? "AmberBrush" : "NpuBrush"];
                }
                text.Append(update.Text);
                last = update;

                TokenCountText.Text = update.Tokens.ToString();
                if (update.Mode != InferenceMode.Replay)
                {
                    TokenProgressText.Text = $"{update.TokensPerSecond:F1}";
                    FirstTokenText.Text = $"{update.FirstTokenMs / 1000:F2}s";
                }
                else
                {
                    TokenProgressText.Text = "replay";
                    FirstTokenText.Text = "-";
                }
                if ((DateTime.UtcNow - _lastRender).TotalMilliseconds > 80)
                {
                    MarkdownRenderer.Render(OutputText, text.ToString(), accent);
                    OutputScroll.ChangeView(null, OutputScroll.ScrollableHeight, null, true);
                    _lastRender = DateTime.UtcNow;
                }
            }
        }
        catch (OperationCanceledException)
        {
            text.Append("\n\n*Stopped by presenter.*");
        }
        catch (Exception ex)
        {
            AppLog.Write($"Run failed: {ex}");
            text.Append($"\n\nSomething went wrong: {ex.Message}");
        }
        finally
        {
            MarkdownRenderer.Render(OutputText, text.ToString(), accent);
            _cts.Dispose();
            _cts = null;
            RunButton.IsEnabled = true;
            StopButton.IsEnabled = false;
            if (last?.Mode == InferenceMode.Replay)
                TokenProgressText.Text = "replay";
            UpdateStatus();
            FocusPrompt();
        }
    }

    private void ShowMode(GenerationUpdate update)
    {
        OutputModeBadge.Visibility = Visibility.Visible;
        switch (update.Mode)
        {
            case InferenceMode.LiveNpu:
                OutputModeText.Text = $"LIVE  ·  NPU  ·  {_runtime.ModelAlias}";
                OutputModeText.Foreground = (Brush)Application.Current.Resources["NpuBrush"];
                break;
            case InferenceMode.LiveGpu:
                OutputModeText.Text = $"LIVE  ·  GPU (APPROVED)  ·  {_runtime.ModelAlias}";
                OutputModeText.Foreground = (Brush)Application.Current.Resources["BlueBrush"];
                break;
            default:
                var match = _runtime.Replay.Find(PromptBox.Text);
                OutputModeText.Text = match is null
                    ? "REPLAY  ·  NO RECORDING"
                    : $"REPLAY  ·  RECORDED ON {match.Device} {match.RecordedAt:dd MMM HH:mm}";
                OutputModeText.Foreground = (Brush)Application.Current.Resources["AmberBrush"];
                break;
        }
        if (!string.IsNullOrEmpty(update.Note))
        {
            OutputNoteText.Text = update.Note;
            OutputNoteText.Visibility = Visibility.Visible;
        }
    }

    private async void RunButton_Click(object sender, RoutedEventArgs e) => await RunAsync();

    private void StopButton_Click(object sender, RoutedEventArgs e) => _cts?.Cancel();

    private void RunAccelerator_Invoked(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args) =>
        args.Handled = HandlePresenterKey(sender.Key, sender.Modifiers.HasFlag(Windows.System.VirtualKeyModifiers.Control));

    private void StopAccelerator_Invoked(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args) =>
        args.Handled = HandlePresenterKey(sender.Key, false);

    private void SampleAccelerator_Invoked(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args) =>
        args.Handled = HandlePresenterKey(sender.Key, true);

    private void ReplayAccelerator_Invoked(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args) =>
        args.Handled = HandlePresenterKey(sender.Key, true);

    private void ReplaySwitch_Toggled(object sender, RoutedEventArgs e)
    {
        _runtime.ForceReplay = ReplaySwitch.IsOn;
        UpdateStatus();
    }

    private void FullScreenAccelerator_Invoked(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args) =>
        args.Handled = HandlePresenterKey(sender.Key, false);

    // ── About + consent ───────────────────────────────────────────────────

    private void AboutButton_Click(object sender, RoutedEventArgs e)
    {
        var frame = new Frame();
        frame.Navigate(typeof(AboutPage));
        if (frame.Content is AboutPage page)
            page.UpdateTransparency(_runtime.ModelId, _runtime.State == RuntimeState.Ready, _npuNow, _runtime.LastTokensPerSecond);
        var window = new Window { Title = $"About {_runtime.Config.AppTitle}", Content = frame };
        window.AppWindow.Resize(new Windows.Graphics.SizeInt32(720, 760));
        window.Activate();
    }

    /// <summary>GPU is never used silently: the presenter must approve it here.</summary>
    private async Task<bool> AskGpuConsentAsync(string modelName, string reason, CancellationToken ct)
    {
        var tcs = new TaskCompletionSource<bool>();
        DispatcherQueue.TryEnqueue(async () =>
        {
            try
            {
                var dialog = new ContentDialog
                {
                    XamlRoot = Content.XamlRoot,
                    Title = "Use the GPU instead of the NPU?",
                    Content = $"{modelName} could not run on the NPU: {reason}.\n\nAllow this demo to run it on the GPU? It still runs entirely on this device. Choose No to use the recorded replay safety net instead.",
                    PrimaryButtonText = "Allow GPU",
                    CloseButtonText = "No, stay NPU-only",
                    DefaultButton = ContentDialogButton.Close
                };
                tcs.TrySetResult(await dialog.ShowAsync() == ContentDialogResult.Primary);
            }
            catch (Exception ex)
            {
                AppLog.Write($"Consent dialog failed: {ex.Message}");
                tcs.TrySetResult(false);
            }
        });
        return await tcs.Task;
    }
}
