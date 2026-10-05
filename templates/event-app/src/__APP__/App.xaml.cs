using __APP__.Services;
using Microsoft.UI.Xaml;

namespace __APP__;

public partial class App : Application
{
    public static MainWindow? MainWindow { get; private set; }

    public App()
    {
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
        if (Environment.GetCommandLineArgs().Any(a => a.Equals("--warmup", StringComparison.OrdinalIgnoreCase)))
        {
            // Headless preflight: compile the pinned model for the NPU in this app's own
            // cache and record replay outputs, then exit. Run by the harness before the show.
            var code = await WarmupRunner.RunAsync();
            Environment.Exit(code);
            return;
        }

        MainWindow = new MainWindow();
        MainWindow.Activate();
        _ = LocalAiRuntime.Instance.InitializeAsync();
    }
}
