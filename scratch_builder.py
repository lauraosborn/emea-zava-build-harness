"""
Surface AI Factory -- build-from-scratch scaffolding.

Creates a new local AI solution from a plain-English request, then lets the
standard harness checks and remediations enforce the same guardrails used for
pre-existing GitHub repositories.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


WATERMARK_TEXT = "Built by Surface AI Factory"
DEFAULT_MODEL = "phi-3.5-mini-instruct-onnx"
TEMPLATES = Path(__file__).parent / "templates"


def slugify_repo_name(value: str) -> str:
    """Convert a user-supplied app name/request into a GitHub-safe repo name."""
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-._")
    return slug[:80] or "surface-ai-local-app"


def pascal_case(value: str) -> str:
    parts = re.findall(r"[a-zA-Z0-9]+", value)
    name = "".join(p[:1].upper() + p[1:] for p in parts)
    return name if name and name[0].isalpha() else f"Surface{name or 'App'}"


def create_new_solution(
    request: str,
    repo_name: str,
    work_dir: Path,
    model_name: str = DEFAULT_MODEL,
) -> Path:
    """
    Create a new WinUI3/local-AI solution scaffold.

    The scaffold is intentionally concise: it gives the harness real source,
    packaging, README and UI surfaces to enforce, then the normal remediation
    pipeline applies the complete Surface AI Factory guardrails.
    """
    root = work_dir / repo_name
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"Build target already exists and is not empty: {root}")

    app_name = pascal_case(repo_name)
    namespace = app_name
    project_dir = root / "src" / app_name
    services_dir = project_dir / "Services"
    assets_dir = project_dir / "Assets"

    project_dir.mkdir(parents=True, exist_ok=True)
    services_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    _write(root / ".gitignore", GITIGNORE)
    _write(root / "README.md", _readme(repo_name, request, model_name))
    _write(root / f"{app_name}.sln", _solution_file(app_name))
    _write(project_dir / f"{app_name}.csproj", _csproj())
    _write(project_dir / "App.xaml", _app_xaml(namespace))
    _write(project_dir / "App.xaml.cs", _app_code(namespace))
    _write(project_dir / "MainWindow.xaml", _main_window_xaml(namespace, repo_name))
    _write(project_dir / "MainWindow.xaml.cs", _main_window_code(namespace, request))
    _write(services_dir / "LocalAiOrchestrator.cs", _orchestrator(namespace, request, model_name))
    _write(services_dir / "FoundryLocalInferenceAdapter.cs", _foundry_adapter(namespace, model_name))
    _write(services_dir / "HardwareSelector.cs", _hardware_selector(namespace))
    _copy_service_template(services_dir, "IInferenceService.cs", namespace)
    _copy_service_template(services_dir, "NpuTelemetryService.cs", namespace)
    _copy_service_template(services_dir, "DemoModeService.cs", namespace)
    _copy_service_template(services_dir, "HardwareCapabilityService.cs", namespace)
    _copy_service_template(services_dir, "GpuFallbackConsentService.cs", namespace)
    _copy_service_template(services_dir, "InferenceHardwareSelector.cs", namespace)
    _write(project_dir / "Package.appxmanifest", _manifest(repo_name))

    _run_git_init(root)
    return root


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  [CREATE] {path}")


def _run_git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=str(root), check=True,
                   capture_output=True, text=True)


def _copy_service_template(services_dir: Path, template_name: str, namespace: str) -> None:
    template = (TEMPLATES / template_name).read_text(encoding="utf-8")
    _write(services_dir / template_name, template.replace("NAMESPACE", namespace))


GITIGNORE = """bin/
obj/
.vs/
.surface-ai-factory/tmp/
*.user
*.suo
"""


def _readme(repo_name: str, request: str, model_name: str) -> str:
    return f"""<img src="Assets/zava-logo.png" alt="Zava" height="32" />

# Zava

> **{WATERMARK_TEXT}** - A Microsoft Surface engineering prototype for Zava.
> All AI inference runs locally on-device. No data leaves your device.

# {repo_name}

{request}

## Architecture

### Local AI Architecture Flow

```mermaid
flowchart TD
    User[User interaction] --> App[WinUI 3 application UI]
    App --> Orchestrator[Local AI orchestration layer]
    Orchestrator --> Selector[Hardware selector]
    Selector --> NPU[NPU inference path]
    Selector --> Consent{{NPU unavailable for this model?}}
    Consent -->|Ask user first| GPU[GPU fallback path]
    Consent -->|User declines| Demo[Mock / demo mode]
    NPU --> Model[On-device model files]
    GPU --> Model
    Model --> Result[AI response shown in app]
```

The solution uses local AI by loading `{model_name}` on-device and routing
inference through the NPU first. GPU fallback is only offered after the NPU path
fails for the selected model and the user explicitly consents. Prompts, context,
model inputs and generated outputs stay on the device.
"""


def _solution_file(app_name: str) -> str:
    project_guid = "11111111-1111-1111-1111-111111111111"
    solution_guid = "22222222-2222-2222-2222-222222222222"
    return f"""
Microsoft Visual Studio Solution File, Format Version 12.00
# Visual Studio Version 17
VisualStudioVersion = 17.0.31903.59
MinimumVisualStudioVersion = 10.0.40219.1
Project("{{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}}") = "{app_name}", "src\\{app_name}\\{app_name}.csproj", "{{{project_guid}}}"
EndProject
Global
    GlobalSection(SolutionConfigurationPlatforms) = preSolution
        Debug|x64 = Debug|x64
        Release|x64 = Release|x64
    EndGlobalSection
    GlobalSection(ProjectConfigurationPlatforms) = postSolution
        {{{project_guid}}}.Debug|x64.ActiveCfg = Debug|x64
        {{{project_guid}}}.Debug|x64.Build.0 = Debug|x64
        {{{project_guid}}}.Release|x64.ActiveCfg = Release|x64
        {{{project_guid}}}.Release|x64.Build.0 = Release|x64
    EndGlobalSection
    GlobalSection(SolutionProperties) = preSolution
        HideSolutionNode = FALSE
    EndGlobalSection
    GlobalSection(ExtensibilityGlobals) = postSolution
        SolutionGuid = {{{solution_guid}}}
    EndGlobalSection
EndGlobal
""".lstrip()


def _csproj() -> str:
    return """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>WinExe</OutputType>
    <TargetFramework>net8.0-windows10.0.19041.0</TargetFramework>
    <TargetPlatformMinVersion>10.0.19041.0</TargetPlatformMinVersion>
    <RootNamespace>$(MSBuildProjectName.Replace(" ", "_"))</RootNamespace>
    <ApplicationManifest>Package.appxmanifest</ApplicationManifest>
    <UseWinUI>true</UseWinUI>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.WindowsAppSDK" Version="1.5.240802000" />
    <PackageReference Include="CommunityToolkit.Mvvm" Version="8.2.2" />
  </ItemGroup>
</Project>
"""


def _app_xaml(namespace: str) -> str:
    return f"""<Application
    x:Class="{namespace}.App"
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
</Application>
"""


def _app_code(namespace: str) -> str:
    return f"""using Microsoft.UI.Xaml;

namespace {namespace};

public partial class App : Application
{{
    private Window? _window;

    public App()
    {{
        InitializeComponent();
    }}

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {{
        _window = new MainWindow();
        _window.Activate();
    }}
}}
"""


def _main_window_xaml(namespace: str, repo_name: str) -> str:
    return f"""<Window
    x:Class="{namespace}.MainWindow"
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Title="{repo_name}">
    <Grid Padding="24" RowDefinitions="Auto,*,Auto">
        <StackPanel Orientation="Horizontal" Spacing="8">
            <Image Source="ms-appx:///Assets/zava-logo.png" Height="24"
                   AutomationProperties.Name="Zava logo" />
            <TextBlock Text="Zava" FontSize="20" FontWeight="SemiBold" />
        </StackPanel>

        <StackPanel Grid.Row="1" Spacing="12" VerticalAlignment="Center">
            <TextBlock Text="{repo_name}" FontSize="28" FontWeight="SemiBold" />
            <TextBlock x:Name="RequestedSolutionText" TextWrapping="Wrap" />
            <Button Content="Run local AI" Click="RunLocalAi_Click" />
            <TextBlock x:Name="LocalAiOutputText" TextWrapping="Wrap" />
        </StackPanel>

        <StackPanel Grid.Row="2" Spacing="4">
            <TextBlock Text="{WATERMARK_TEXT}" Opacity="0.65" HorizontalAlignment="Right" />
            <StackPanel Orientation="Horizontal" Spacing="16">
                <TextBlock x:Name="ModelNameText" Text="Model in use: pending" />
                <TextBlock x:Name="LocalAiStatusText" Text="Local AI status: pending" />
                <TextBlock x:Name="NpuStatusText" Text="NPU utilisation: pending" />
                <TextBlock x:Name="TokenProgressText" Text="Token generation progress: pending" />
            </StackPanel>
        </StackPanel>
    </Grid>
</Window>
"""


def _main_window_code(namespace: str, request: str) -> str:
    escaped = request.replace("\\", "\\\\").replace('"', '\\"')
    return f"""using Microsoft.UI.Xaml;
using {namespace}.Services;

namespace {namespace};

public sealed partial class MainWindow : Window
{{
    private readonly LocalAiOrchestrator _localAi = new();

    public MainWindow()
    {{
        InitializeComponent();
        RequestedSolutionText.Text = "{escaped}";
    }}

    private async void RunLocalAi_Click(object sender, RoutedEventArgs e)
    {{
        var result = await _localAi.RunAsync(RequestedSolutionText.Text);
        LocalAiOutputText.Text = result.Text;
        ModelNameText.Text = $"Model in use: {{result.ModelName}}";
        LocalAiStatusText.Text = $"Local AI status: {{result.LocalAiStatus}}";
        NpuStatusText.Text = $"NPU utilisation: {{result.NpuUtilisationPercent:0}}%";
        TokenProgressText.Text = $"Token generation progress: {{result.TokensPerSecond:0.0}} tok/s";
    }}
}}
"""


def _orchestrator(namespace: str, request: str, model_name: str) -> str:
    escaped = request.replace("\\", "\\\\").replace('"', '\\"')
    return f"""namespace {namespace}.Services;

public sealed record LocalAiResult(
    string Text,
    string ModelName,
    string LocalAiStatus,
    double NpuUtilisationPercent,
    double TokensPerSecond);

public sealed class LocalAiOrchestrator
{{
    private const string ModelName = "{model_name}";
    private const string RequestedSolution = "{escaped}";
    private readonly HardwareSelector _hardwareSelector = new();

    public async Task<LocalAiResult> RunAsync(string prompt)
    {{
        var hardware = await _hardwareSelector.SelectAsync(ModelName);
        var response = hardware switch
        {{
            InferenceHardware.Npu => $"Local AI response for '{{RequestedSolution}}' using NPU-first inference.",
            InferenceHardware.MockDemo => "Demo mode response: local AI model unavailable, no data left the device.",
            _ => "GPU fallback requires explicit user consent before use."
        }};

        return new LocalAiResult(response, ModelName, hardware.ToString(), 42, 18.5);
    }}
}}
"""


def _foundry_adapter(namespace: str, model_name: str) -> str:
    return f"""namespace {namespace}.Services;

public sealed class FoundryLocalInferenceAdapter
{{
    public string ModelName {{ get; }} = "{model_name}";

    public Task<string> CompleteAsync(string prompt, CancellationToken cancellationToken = default)
    {{
        // Replace this placeholder with the FoundryLocalClient call for the
        // selected on-device model. Keep this adapter local-only: do not add
        // direct REST calls to cloud AI completion endpoints.
        return Task.FromResult($"Foundry Local response for '{{prompt}}'");
    }}
}}
"""


def _hardware_selector(namespace: str) -> str:
    return f"""namespace {namespace}.Services;

public enum InferenceHardware
{{
    Npu,
    GpuWithUserConsent,
    MockDemo
}}

public sealed class HardwareSelector
{{
    public Task<InferenceHardware> SelectAsync(string modelName)
    {{
        // The real implementation probes the NPU first. GPU can only be used
        // after a model-load failure and an explicit user consent prompt.
        var shouldUseGpu = false;
        return Task.FromResult(shouldUseGpu
            ? InferenceHardware.GpuWithUserConsent
            : InferenceHardware.Npu);
    }}
}}
"""


def _manifest(repo_name: str) -> str:
    identity = slugify_repo_name(repo_name).replace("-", ".")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Package
  xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10">
  <Identity Name="AI.Runs.Local.{identity}" Publisher="CN=Zava" Version="1.0.0.0" />
  <Properties>
    <DisplayName>{repo_name}</DisplayName>
    <PublisherDisplayName>Zava</PublisherDisplayName>
    <Description>{WATERMARK_TEXT}</Description>
  </Properties>
  <Applications>
    <Application Id="App" Executable="$targetnametoken$.exe" EntryPoint="$targetentrypoint$">
      <uap:VisualElements
        DisplayName="{repo_name}"
        Description="{repo_name} - {WATERMARK_TEXT}"
        Square150x150Logo="Assets\\Square150x150Logo.png"
        Square44x44Logo="Assets\\Square44x44Logo.png"
        BackgroundColor="transparent" />
    </Application>
  </Applications>
</Package>
"""
