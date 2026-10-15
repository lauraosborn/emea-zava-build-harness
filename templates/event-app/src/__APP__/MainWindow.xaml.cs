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
using Windows.Storage.Pickers;

namespace __APP__;

public sealed partial class MainWindow : Window
{
    private const int SparkPoints = 60;

    private readonly LocalAiRuntime _runtime = LocalAiRuntime.Instance;
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromMilliseconds(500) };
    private readonly Queue<double> _npuHistory = new(Enumerable.Repeat(0.0, SparkPoints));
    private readonly Dictionary<string, TextBox> _contextBoxes = new();
    private readonly PresenterKeyHook _keyHook;
    private CancellationTokenSource? _cts;
    private bool _sampling;
    private double _npuNow;
    private DateTime _lastRender = DateTime.MinValue;
    private ModeConfig _mode = new();
    private AppRequest? _request;

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
        // The answer card fills the visible area, so scrolling to it shows the whole answer.
        MainScroll.SizeChanged += (_, _) => OutputCard.MinHeight = Math.Max(380, MainScroll.ViewportHeight - 8);
        Activated += (_, e) =>
        {
            if (e.WindowActivationState != WindowActivationState.Deactivated)
                FocusPrompt();
        };

        UpdateNetwork();
        UpdateStatus();
        _keyHook = new PresenterKeyHook(this, HandlePresenterKey);
    }

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
            case Windows.System.VirtualKey.I when ctrl:
                ToggleDetails();
                return true;
            case Windows.System.VirtualKey.L when ctrl:
                ClearSession();
                return true;
            case >= Windows.System.VirtualKey.F2 and <= Windows.System.VirtualKey.F4:
                var modeIndex = key - Windows.System.VirtualKey.F2;
                if (modeIndex < ModeBar.Items.Count)
                    ModeBar.SelectedItem = ModeBar.Items[modeIndex];
                return true;
            case >= Windows.System.VirtualKey.Number1 and <= Windows.System.VirtualKey.Number3 when ctrl:
                var index = key - Windows.System.VirtualKey.Number1;
                if (index < _mode.SamplePrompts.Count)
                    PromptBox.Text = _mode.SamplePrompts[index];
                FocusPrompt();
                return true;
            default:
                return false;
        }
    }

    private void Accelerator_Invoked(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args) =>
        args.Handled = HandlePresenterKey(sender.Key, sender.Modifiers.HasFlag(Windows.System.VirtualKeyModifiers.Control));

    private void ToggleFullScreen() =>
        AppWindow.SetPresenter(AppWindow.Presenter.Kind == AppWindowPresenterKind.FullScreen
            ? AppWindowPresenterKind.Overlapped
            : AppWindowPresenterKind.FullScreen);

    private void ToggleDetails() =>
        DetailsDrawer.Visibility = DetailsDrawer.Visibility == Visibility.Visible ? Visibility.Collapsed : Visibility.Visible;

    private void FocusPrompt() =>
        DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Low, () =>
        {
            if (PromptBox.XamlRoot is not null)
                PromptBox.Focus(FocusState.Programmatic);
        });

    private Style Res(string key) => (Style)Application.Current.Resources[key];
    private Brush BrushRes(string key) => (Brush)Application.Current.Resources[key];

    private void ConfigureWindow()
    {
        Title = _runtime.Config.AppTitle;
        WatermarkText.Text = BrandingConstants.Watermark;
        try
        {
            AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "AppIcon.ico"));
            if (AppWindow.TitleBar is { } bar)
            {
                var canvas = ThemeColors.Parse(_runtime.Config.Theme.Canvas);
                var ink = ThemeColors.Parse(_runtime.Config.Theme.Ink);
                bar.BackgroundColor = canvas;
                bar.ButtonBackgroundColor = canvas;
                bar.InactiveBackgroundColor = canvas;
                bar.ButtonInactiveBackgroundColor = canvas;
                bar.ForegroundColor = ink;
                bar.ButtonForegroundColor = ink;
            }
            if (AppWindow.Presenter is OverlappedPresenter presenter)
                presenter.Maximize();
        }
        catch (Exception ex)
        {
            AppLog.Write($"Window configuration failed: {ex.Message}");
        }
    }

    // ── What the app is ───────────────────────────────────────────────────

    private void ApplyConfig()
    {
        var config = _runtime.Config;
        AppTitleText.Text = config.AppTitle;
        TaglineText.Text = config.Tagline;
        OrganisationText.Text = config.Organisation.ToUpperInvariant();
        DomainText.Text = config.Brand.Domain.ToUpperInvariant();
        BrandMarkIcon.Glyph = string.IsNullOrWhiteSpace(config.Brand.MarkGlyph) ? "\uE946" : config.Brand.MarkGlyph;
        WorkflowHeadingText.Text = config.Brand.WorkflowLabel;

        foreach (var language in config.Languages)
            LanguageBox.Items.Add(language);

        BuildContextFields(config);
        BuildDrivers(config);
        BuildWorkspace(config);
        BuildKnowledgeCard();
        ConfigurePrototypeMedia(config);

        var modes = config.EffectiveModes;
        BuildWorkflowCards(modes);
        for (var i = 0; i < modes.Count; i++)
            ModeBar.Items.Add(new SelectorBarItem { Text = modes[i].Name, Tag = modes[i] });
        ModeBar.Visibility = modes.Count > 1 ? Visibility.Visible : Visibility.Collapsed;
        if (ModeBar.Items.Count > 0)
            ModeBar.SelectedItem = ModeBar.Items[0];

        foreach (var reason in config.SelectionReasons)
            ReasonsPanel.Children.Add(new TextBlock { Text = "· " + reason, Style = Res("Body"), FontSize = 12 });
    }

    private void BuildWorkspace(AppConfig config)
    {
        if (config.Workspace.Items.Count == 0)
            return;
        WorkspacePanel.Visibility = Visibility.Visible;
        WorkspaceTitleText.Text = string.IsNullOrWhiteSpace(config.Workspace.Title) ? "Work queue" : config.Workspace.Title;
        WorkspaceSubtitleText.Text = config.Workspace.Subtitle;
        WorkspaceItemsPanel.Children.Clear();
        foreach (var item in config.Workspace.Items)
        {
            var button = new Button
            {
                HorizontalAlignment = HorizontalAlignment.Stretch,
                HorizontalContentAlignment = HorizontalAlignment.Stretch,
                Padding = new Thickness(0),
                Background = BrushRes("PanelBrush"),
                BorderBrush = BrushRes("StrokeBrush"),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(12)
            };
            var grid = new Grid { Padding = new Thickness(12), ColumnSpacing = 10 };
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            var stack = new StackPanel { Spacing = 2 };
            stack.Children.Add(new TextBlock { Text = item.Title, Foreground = BrushRes("InkBrush"), FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap });
            stack.Children.Add(new TextBlock { Text = item.Subtitle, Style = Res("Body"), FontSize = 12 });
            grid.Children.Add(stack);
            var status = new StackPanel { Spacing = 2, HorizontalAlignment = HorizontalAlignment.Right };
            status.Children.Add(new TextBlock { Text = item.Status, Foreground = BrushRes("AccentBrush"), FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, FontSize = 12, HorizontalAlignment = HorizontalAlignment.Right });
            status.Children.Add(new TextBlock { Text = item.Priority, Style = Res("MetricLabel"), HorizontalAlignment = HorizontalAlignment.Right });
            Grid.SetColumn(status, 1);
            grid.Children.Add(status);
            button.Content = grid;
            button.Click += (_, _) => SelectWorkspaceItem(item);
            WorkspaceItemsPanel.Children.Add(button);
        }
        SelectWorkspaceItem(config.Workspace.Items[0]);
    }

    private void SelectWorkspaceItem(WorkspaceItemConfig item)
    {
        foreach (var field in item.Context)
            if (_contextBoxes.TryGetValue(field.Key, out var box))
                box.Text = field.Value;
        if (!string.IsNullOrWhiteSpace(item.Prompt))
            PromptBox.Text = item.Prompt;
        FocusPrompt();
    }

    private void ConfigurePrototypeMedia(AppConfig config)
    {
        if (!config.Prototype.EnablesImageInput)
            return;
        MediaPanel.Visibility = Visibility.Visible;
        DemoImageLabelText.Text = string.IsNullOrWhiteSpace(config.Prototype.DemoImageLabel)
            ? "Preloaded demo image"
            : config.Prototype.DemoImageLabel;
        if (!string.IsNullOrWhiteSpace(config.Prototype.DemoImage))
        {
            var uri = new Uri("ms-appx:///" + config.Prototype.DemoImage.Replace("\\", "/"));
            DemoImage.Source = config.Prototype.DemoImage.EndsWith(".svg", StringComparison.OrdinalIgnoreCase)
                ? new Microsoft.UI.Xaml.Media.Imaging.SvgImageSource(uri)
                : new Microsoft.UI.Xaml.Media.Imaging.BitmapImage(uri);
        }
    }

    private void BuildWorkflowCards(IReadOnlyList<ModeConfig> modes)
    {
        WorkflowCardsPanel.Children.Clear();
        for (var i = 0; i < modes.Count; i++)
        {
            var mode = modes[i];
            var card = new Border { Style = (Style)Application.Current.Resources["WorkflowCard"] };
            var grid = new Grid { ColumnSpacing = 12 };
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            grid.Children.Add(new Border
            {
                Width = 38, Height = 38, CornerRadius = new CornerRadius(12), Background = BrushRes("AccentSoftBrush"),
                Child = new FontIcon { Glyph = string.IsNullOrWhiteSpace(mode.Icon) ? "\uE946" : mode.Icon, FontSize = 18, Foreground = BrushRes("AccentBrush") }
            });
            var text = new StackPanel { Spacing = 2 };
            text.Children.Add(new TextBlock { Text = mode.Name, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, Foreground = BrushRes("InkBrush"), TextWrapping = TextWrapping.Wrap });
            text.Children.Add(new TextBlock { Text = string.IsNullOrWhiteSpace(mode.Description) ? mode.InputLabel : mode.Description, Style = Res("Body"), FontSize = 12 });
            Grid.SetColumn(text, 1);
            grid.Children.Add(text);
            var shortcut = new TextBlock { Text = $"{i + 1}", Style = Res("MetricLabel"), FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center };
            Grid.SetColumn(shortcut, 2);
            grid.Children.Add(shortcut);
            card.Child = grid;
            var index = i;
            card.Tapped += (_, _) =>
            {
                if (index < ModeBar.Items.Count)
                    ModeBar.SelectedItem = ModeBar.Items[index];
            };
            WorkflowCardsPanel.Children.Add(card);
        }
    }

    private void BuildContextFields(AppConfig config)
    {
        ContextTitleText.Text = string.IsNullOrWhiteSpace(config.ContextTitle) ? "Details" : config.ContextTitle;
        for (var i = 0; i < config.ContextFields.Count; i++)
        {
            var field = config.ContextFields[i];
            ContextGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var box = new TextBox { Header = field.Label, PlaceholderText = field.Placeholder };
            Grid.SetColumn(box, i);
            ContextGrid.Children.Add(box);
            _contextBoxes[field.Key] = box;
        }
    }

    private void BuildDrivers(AppConfig config)
    {
        var drivers = config.LocalDrivers.Count > 0 ? config.LocalDrivers : new List<LocalDriver>
        {
            new() { Id = "privacy", Title = "Private by design", Detail = "Your data stays on this PC. Nothing is sent to a cloud AI service." },
            new() { Id = "offline", Title = "Works without the internet", Detail = "Answers keep coming when the network is down." }
        };
        LocalStoryRibbonText.Text = "Local: " + string.Join(" · ", drivers.Take(3).Select(d => d.Title));
    }

    private void BuildKnowledgeCard()
    {
        var kb = _runtime.Engine.Knowledge;
        if (kb.PassageCount == 0)
            return;
        KnowledgeCard.Visibility = Visibility.Visible;
        KnowledgeStatsText.Text = $"{kb.DocumentCount} of {_runtime.Config.Organisation}'s own documents, searched on this device. Update them by editing the files in Assets\\knowledge.";
        foreach (var doc in kb.Chunks.GroupBy(c => (c.Document, c.Scope)))
            KnowledgeList.Children.Add(new TextBlock
            {
                Style = Res("Body"), FontSize = 13,
                Text = $"·  {doc.Key.Document}{(doc.Key.Scope == "staff" ? "  (staff only)" : "")}"
            });
    }

    private void ModeBar_SelectionChanged(SelectorBar sender, SelectorBarSelectionChangedEventArgs args)
    {
        if (sender.SelectedItem?.Tag is ModeConfig mode)
            SetMode(mode);
    }

    private void SetMode(ModeConfig mode)
    {
        _mode = mode;
        InputLabelText.Text = mode.InputLabel;
        RunButtonText.Text = mode.Action;
        OutputTitleText.Text = mode.OutputTitle;
        ContextPanel.Visibility = mode.UsesContext && _contextBoxes.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        LanguagePanel.Visibility = mode.UsesLanguage && LanguageBox.Items.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        if (mode.UsesLanguage && LanguageBox.SelectedItem is null && LanguageBox.Items.Count > 0)
            LanguageBox.SelectedItem = LanguageBox.Items.Contains(mode.DefaultLanguage) ? mode.DefaultLanguage : LanguageBox.Items[0];

        SamplesPanel.Children.Clear();
        for (var i = 0; i < mode.SamplePrompts.Count && i < 3; i++)
        {
            var prompt = mode.SamplePrompts[i];
            var button = new Button
            {
                HorizontalAlignment = HorizontalAlignment.Stretch,
                HorizontalContentAlignment = HorizontalAlignment.Left,
                Padding = new Thickness(12, 8, 12, 8),
                Content = new TextBlock { Text = prompt, TextTrimming = TextTrimming.CharacterEllipsis, FontSize = 13 }
            };
            ToolTipService.SetToolTip(button, $"Ctrl+{i + 1}");
            button.Click += (_, _) =>
            {
                PromptBox.Text = prompt;
                FocusPrompt();
            };
            SamplesPanel.Children.Add(button);
        }
        PromptBox.Text = mode.SamplePrompts.FirstOrDefault() ?? "";
        ClearOutput();
        FocusPrompt();
    }

    /// <summary>Next person: wipe private context, the request and the answer from memory and screen.</summary>
    private void ClearSession()
    {
        foreach (var box in _contextBoxes.Values)
            box.Text = "";
        PromptBox.Text = "";
        ClearOutput();
        FocusPrompt();
    }

    private void ClearOutput()
    {
        OutputText.Blocks.Clear();
        OutputNoteText.Visibility = Visibility.Collapsed;
        OutputModeBadge.Visibility = Visibility.Collapsed;
        SourcesPanel.Visibility = Visibility.Collapsed;
        OutputPlaceholder.Visibility = Visibility.Visible;
        _request = null;
    }

    private void ClearButton_Click(object sender, RoutedEventArgs e) => ClearSession();

    private void DetailsButton_Click(object sender, RoutedEventArgs e) => ToggleDetails();

    private void UseDemoImageButton_Click(object sender, RoutedEventArgs e)
    {
        var prompt = _runtime.Config.Prototype.DemoImagePrompt;
        if (!string.IsNullOrWhiteSpace(prompt))
            PromptBox.Text = prompt;
        FocusPrompt();
    }

    private async void UploadImageButton_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var picker = new FileOpenPicker();
            WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(this));
            picker.FileTypeFilter.Add(".png");
            picker.FileTypeFilter.Add(".jpg");
            picker.FileTypeFilter.Add(".jpeg");
            picker.FileTypeFilter.Add(".bmp");
            picker.FileTypeFilter.Add(".webp");
            picker.SuggestedStartLocation = PickerLocationId.PicturesLibrary;
            var file = await picker.PickSingleFileAsync();
            if (file is null)
                return;
            var template = _runtime.Config.Prototype.UploadPromptTemplate;
            PromptBox.Text = string.IsNullOrWhiteSpace(template)
                ? $"Uploaded image: {file.Name}. Analyse it."
                : template.Replace("{fileName}", file.Name);
            FocusPrompt();
        }
        catch (Exception ex)
        {
            AppLog.Write($"Image upload picker failed: {ex.Message}");
            PromptBox.Text = "Uploaded component image: uploaded-component.jpg. Inspect the visible manufacturing defect and recommend next checks.";
            FocusPrompt();
        }
    }

    private void ShowSources(AppRequest request, double retrievalMs)
    {
        SourcesList.Children.Clear();
        if (_runtime.Engine.Knowledge.PassageCount == 0 || request.Mode.TopPassages == 0)
            return;
        SourcesTitle.Text = request.Sources.Count > 0
            ? $"Based on {request.Sources.Count} passages from {_runtime.Config.Organisation}'s documents, found on this device in {retrievalMs:F0} ms"
            : "Nothing in the knowledge base matched this request";
        foreach (var s in request.Sources)
            SourcesList.Children.Add(new TextBlock { Style = Res("Body"), FontSize = 12, Text = $"·  {s.Chunk.Document}: {s.Chunk.Section}" });
        SourcesPanel.Visibility = Visibility.Visible;
    }

    // ── Live telemetry (ribbon + details drawer) ──────────────────────────

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
        if (DetailsDrawer.Visibility == Visibility.Visible)
            DrawSparkline();
        NpuStatusText.Text = _runtime.NpuMonitor.Available ? $"NPU: {_npuNow:F0}%" : "NPU: counters unavailable";
        NpuPeakText.Text = $"peak {_runtime.NpuMonitor.PeakPercent:F0}%";
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
        var ep = string.IsNullOrEmpty(rt.ExecutionProvider) ? "" : rt.ExecutionProvider.Replace("ExecutionProvider", "") + "  ·  ";
        var size = rt.Config.Primary.SizeMb > 0 && !rt.UsingBackup ? $"{rt.Config.Primary.SizeMb / 1024.0:F2} GB  ·  " : "";
        var license = rt.UsingBackup ? rt.Config.Backup?.License : rt.Config.Primary.License;
        ModelMetaText.Text = $"{ep}{size}{license}";
        LocalAiStatusText.Text = $"{BrandingConstants.LocalAiStatusLabel} {rt.Status}";
        var loading = rt.State is RuntimeState.Starting or RuntimeState.Preparing or RuntimeState.Downloading;
        LoadProgress.IsActive = loading;
        LoadProgress.Visibility = loading ? Visibility.Visible : Visibility.Collapsed;
        LoadTimeText.Text = rt.State == RuntimeState.Ready ? $"ready in {rt.LoadMs / 1000:F1}s" : "-";

        NpuNameText.Text = $"NPU  {(rt.Telemetry.NpuDetected ? rt.Telemetry.NpuName : "not detected")}";
        CpuNameText.Text = $"CPU  {rt.Telemetry.CpuName}";

        var (label, brush) = rt.ForceReplay
            ? ("REPLAY", "AmberBrush")
            : rt.State switch
            {
                RuntimeState.Ready when rt.Device == "NPU" => ("NPU", "NpuBrush"),
                RuntimeState.Ready => (rt.Device.ToUpperInvariant(), "BlueBrush"),
                RuntimeState.Replay => ("REPLAY", "AmberBrush"),
                _ => ("Loading", "InkMutedBrush")
            };
        DeviceBadgeText.Text = label;
        DeviceBadgeText.Foreground = BrushRes(brush);
        DeviceChipText.Text = label;
        DeviceChipText.Foreground = DeviceBadgeText.Foreground;

        RibbonStatusText.Text = rt.ForceReplay ? "Safety net on: answers are labelled replays"
            : rt.State switch
            {
                RuntimeState.Ready => $"{rt.ModelAlias}{(rt.UsingBackup ? " (backup)" : "")} on this PC",
                RuntimeState.Replay => "Local model unavailable: labelled replays only",
                RuntimeState.Downloading => rt.Status,
                _ => rt.Status
            };
        ReadyText.Text = rt.State == RuntimeState.Ready || rt.ForceReplay ? "" : "Getting ready...";

        HistoryPanel.Children.Clear();
        foreach (var r in rt.History.Take(4))
        {
            var where = r.Mode switch { InferenceMode.LiveNpu => "NPU", InferenceMode.LiveGpu => "GPU", _ => "replay" };
            HistoryPanel.Children.Add(new TextBlock
            {
                Style = Res("Mono"), FontSize = 11,
                Text = $"{r.At:HH:mm:ss}  {where}  {r.Tokens} tok  {r.TokensPerSecond:F1} tok/s  first {r.FirstTokenMs / 1000:F1}s  peak {r.PeakNpuPercent:F0}%"
            });
        }
        UpdateTransparencyLine();
    }

    private void UpdateTransparencyLine()
    {
        var rt = _runtime;
        var speed = rt.LastTokensPerSecond > 0 ? $"  ·  {rt.LastTokensPerSecond:F1} tok/s" : "";
        var npu = rt.NpuMonitor.Available ? $"  ·  NPU {_npuNow:F0}%" : "";
        TransparencyText.Text = $"Local AI: {(rt.State == RuntimeState.Ready ? "on-device" : "starting")}{npu}{speed}";
    }

    private void UpdateNetwork()
    {
        try
        {
            var profile = NetworkInformation.GetInternetConnectionProfile();
            var online = profile?.GetNetworkConnectivityLevel() == NetworkConnectivityLevel.InternetAccess;
            NetworkText.Text = online ? "Online" : "Offline, still working";
            NetworkText.Foreground = BrushRes(online ? "InkSecondaryBrush" : "NpuBrush");
        }
        catch
        {
            NetworkText.Text = "Network unknown";
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
        OutputPlaceholder.Visibility = Visibility.Collapsed;
        OutputText.Blocks.Clear();

        var context = _contextBoxes.ToDictionary(kv => kv.Key, kv => kv.Value.Text);
        var retrieval = System.Diagnostics.Stopwatch.StartNew();
        _request = _runtime.Engine.Build(_mode, prompt, context, _runtime.Config.ContextFields,
            LanguageBox.SelectedItem as string, DateTime.Now);
        ShowSources(_request, retrieval.Elapsed.TotalMilliseconds);
        OutputCard.StartBringIntoView(new BringIntoViewOptions { VerticalAlignmentRatio = 0.0, AnimationDesired = true });

        var accent = BrushRes("AccentBrush");
        var text = new StringBuilder();
        GenerationUpdate? last = null;
        InferenceMode? shownMode = null;

        try
        {
            await foreach (var update in _runtime.GenerateAsync(_request, _cts.Token))
            {
                if (shownMode != update.Mode)
                {
                    if (shownMode is null)
                        OutputCard.StartBringIntoView(new BringIntoViewOptions { VerticalAlignmentRatio = 0.0, AnimationDesired = true });
                    if (shownMode is not null)
                        text.Clear();
                    shownMode = update.Mode;
                    ShowMode(update, running: true);
                    accent = BrushRes(update.Mode == InferenceMode.Replay ? "AmberBrush" : "AccentBrush");
                }
                text.Append(update.Text);
                last = update;

                TokenCountText.Text = $"{update.Tokens} tokens";
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
            text.Append("\n\n*Stopped.*");
        }
        catch (Exception ex)
        {
            AppLog.Write($"Run failed: {ex}");
            text.Append($"\n\nSomething went wrong: {ex.Message}");
        }
        finally
        {
            if (last is not null && last.Mode != InferenceMode.Replay)
            {
                var clean = AnswerCleaner.Clean(text.ToString());
                text.Clear().Append(clean);
            }
            MarkdownRenderer.Render(OutputText, text.ToString(), accent);
            if (last is not null)
                ShowMode(last, running: false);
            _cts.Dispose();
            _cts = null;
            RunButton.IsEnabled = true;
            StopButton.IsEnabled = false;
            UpdateStatus();
            FocusPrompt();
        }
    }

    /// <summary>Live answers say where they ran in plain words; replays are always labelled REPLAY.</summary>
    private void ShowMode(GenerationUpdate update, bool running)
    {
        OutputModeBadge.Visibility = Visibility.Visible;
        switch (update.Mode)
        {
            case InferenceMode.LiveNpu:
            case InferenceMode.LiveGpu:
                OutputModeText.Text = running ? "Answering on this device..." : $"Answered on this device in {update.ElapsedMs / 1000:F1}s";
                OutputModeText.Foreground = BrushRes("NpuBrush");
                break;
            default:
                var match = _runtime.Replay.Find(_request?.ReplayKey ?? PromptBox.Text);
                OutputModeText.Text = match is null
                    ? "REPLAY  ·  no recording for this request"
                    : $"REPLAY  ·  recorded on this device {match.RecordedAt:dd MMM HH:mm}";
                OutputModeText.Foreground = BrushRes("AmberBrush");
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

    private void ReplaySwitch_Toggled(object sender, RoutedEventArgs e)
    {
        _runtime.ForceReplay = ReplaySwitch.IsOn;
        UpdateStatus();
    }

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
                    Content = $"{modelName} could not run on the NPU: {reason}.\n\nAllow it to run on the GPU? It still runs entirely on this device. Choose No to use recorded replays instead.",
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
