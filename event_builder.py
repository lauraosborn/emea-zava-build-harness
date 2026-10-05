"""
Zava Live Build -- event app generator.

Creates a complete, buildable WinUI 3 app from templates/event-app, wired to
the Foundry Local SDK with the pinned NPU model plan. The generated app runs
real on-device inference from the first build: no placeholders, no hardcoded
metrics.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from model_selector import AppSpec, ModelPlan
from scratch_builder import pascal_case

TEMPLATES = Path(__file__).parent / "templates"
EVENT_TEMPLATE = TEMPLATES / "event-app"
WATERMARK_TEXT = "Built by Surface AI Factory"

# Shared Surface AI Factory guardrail templates reused verbatim (NAMESPACE token).
SHARED_SERVICE_TEMPLATES = (
    "IInferenceService.cs",
    "InferenceHardwareSelector.cs",
    "GpuFallbackConsentService.cs",
    "HardwareCapabilityService.cs",
)

TEXT_SUFFIXES = {".cs", ".xaml", ".csproj", ".sln", ".ps1", ".manifest", ".appxmanifest", ".json", ".md"}


def project_name_for(repo_name: str) -> str:
    name = pascal_case(repo_name)
    return name[:48]


def create_event_app(spec: AppSpec, plan: ModelPlan, repo_name: str, work_dir: Path,
                     overwrite: bool = False) -> Path:
    root = work_dir / repo_name
    if root.exists() and any(root.iterdir()):
        if not overwrite:
            raise FileExistsError(f"Build target already exists and is not empty: {root}")
        shutil.rmtree(root, onerror=_force_remove)
    root.mkdir(parents=True, exist_ok=True)

    app = project_name_for(spec.title or repo_name)
    tokens = {
        "__APP_TITLE__": _xml_escape(spec.title),
        "__APP__": app,
        "__PROJECT_GUID__": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{app}.project")).upper(),
        "__SOLUTION_GUID__": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{app}.solution")).upper(),
    }

    _copy_tree(EVENT_TEMPLATE, root, tokens)

    project_dir = root / "src" / app
    for name in SHARED_SERVICE_TEMPLATES:
        _write(project_dir / "Services" / name, _read(TEMPLATES / name).replace("NAMESPACE", app))
    _write(project_dir / "Branding" / "BrandingConstants.cs",
           _read(TEMPLATES / "BrandingConstants.cs").replace("NAMESPACE", app))
    _write(project_dir / "Views" / "AboutPage.xaml",
           _read(TEMPLATES / "AboutPage.xaml").replace("NAMESPACE", app))
    _write(project_dir / "Views" / "AboutPage.xaml.cs",
           _read(TEMPLATES / "AboutPage.xaml.cs").replace("NAMESPACE", app))

    # Root-level brand assets so README/logo checks resolve.
    (root / "Assets").mkdir(exist_ok=True)
    for asset in ("zava-logo.png", "zava-logo.svg"):
        shutil.copyfile(TEMPLATES / "assets" / asset, root / "Assets" / asset)

    config = app_config(spec, plan)
    _write(project_dir / "Assets" / "app-config.json", json.dumps(config, indent=2))
    _write(project_dir / "Assets" / "replay.json", "[]\n")

    meta = root / ".surface-ai-factory"
    _write(meta / "model-plan.json", json.dumps(plan.to_dict(), indent=2))
    _write(meta / "app-spec.json", json.dumps(spec.to_dict(), indent=2))

    _write(root / "models" / "model_catalog.json", json.dumps(model_catalog(plan), indent=2))
    _write(root / "README.md", readme(spec, plan, repo_name, app))
    _write(root / "docs" / "DEMO-RUNBOOK.md", runbook(spec, plan, app))
    _write(root / ".gitignore", GITIGNORE)

    subprocess.run(["git", "init", "-b", "main"], cwd=str(root), check=False,
                   capture_output=True, text=True)
    return root


# ── Config + catalog ────────────────────────────────────────────────────────

def _model_ref(choice) -> dict | None:
    if choice is None:
        return None
    return {
        "alias": choice.alias,
        "modelId": choice.model_id,
        "executionProvider": choice.execution_provider,
        "sizeMb": choice.size_mb,
        "license": choice.license,
    }


def app_config(spec: AppSpec, plan: ModelPlan) -> dict:
    return {
        "appTitle": spec.title,
        "tagline": spec.tagline,
        "idea": plan.analysis.idea,
        "systemPrompt": spec.system_prompt,
        "inputLabel": spec.input_label,
        "samplePrompts": spec.sample_prompts,
        "primary": _model_ref(plan.primary),
        "backup": _model_ref(plan.backup),
        "selectionReasons": plan.primary.reasons if plan.primary else [],
        "firstTokenTimeoutSeconds": 45,
        "maxTokens": 400,
        "temperature": 0.3,
    }


def model_catalog(plan: ModelPlan) -> dict:
    models = []
    for choice in (plan.primary, plan.backup):
        if choice is None:
            continue
        models.append({
            "id": choice.model_id,
            "alias": choice.alias,
            "role": choice.role,
            "version": choice.model_id.rsplit(":", 1)[-1] if ":" in choice.model_id else "",
            "source": "Foundry Local catalog",
            "device": choice.device,
            "executionProvider": choice.execution_provider,
            "format": "ONNX (OpenVINO NPU)" if "openvino" in choice.model_id.lower() else "ONNX",
            "quantization": "INT4/INT8 (NPU-optimised build)",
            "sizeMb": choice.size_mb,
            "license": choice.license,
            "npuOptimized": choice.device == "NPU",
            "requiresGpuFallbackConsent": False,
            "supportedRuntimes": ["foundry-local"],
            "pinned": True,
            "notes": " ".join(choice.reasons),
        })
    return {
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "selectedBy": "Zava Live Build harness",
        "catalogSource": plan.catalog_source,
        "models": models,
    }


# ── Documents ───────────────────────────────────────────────────────────────

def readme(spec: AppSpec, plan: ModelPlan, repo_name: str, app: str) -> str:
    p, b = plan.primary, plan.backup
    primary_row = f"| Primary | `{p.alias}` | `{p.model_id}` | NPU | {p.size_mb / 1024:.2f} GB | {p.license} |" if p else ""
    backup_row = f"| Stage backup | `{b.alias}` | `{b.model_id}` | NPU | {b.size_mb / 1024:.2f} GB | {b.license} |" if b else ""
    reasons = "\n".join(f"- {r}" for r in (p.reasons if p else []))
    samples = "\n".join(f"{i}. {s}" for i, s in enumerate(spec.sample_prompts, 1))
    gaps = "".join(f"\n> **Capability note:** {g.detail} Best available: {g.best_available or 'none'}.\n"
                   for g in plan.gaps)
    return f"""<img src="Assets/zava-logo.png" alt="Zava" height="32" />

# Zava

> **{WATERMARK_TEXT}** - a Microsoft Surface engineering prototype for Zava.
> All AI inference runs locally on-device. No data leaves your device.

# {spec.title}

{spec.tagline}

Generated live by **Zava Live Build** from the idea:

> {plan.analysis.idea}
{gaps}
## Surface Differentiators

- **NPU acceleration with all-day battery**: inference runs on the NPU, leaving the CPU and GPU free.
- **Copilot+ PC features**: on-device AI with no cloud dependency, so the demo works in airplane mode.
- **Privacy by design**: prompts and outputs never leave the device.

## Setup

1. Install [Foundry Local](https://aka.ms/foundry-local) and the .NET 9 SDK.
2. From the harness: `python harness.py --preflight <path-to-this-folder>`
   (downloads the pinned model if needed, builds, compiles it for the NPU and records the replay safety net).
3. Launch: `powershell -File tools\\Start-Demo.ps1`

Or open `{app}.sln` in Visual Studio 2022 and run the `x64` / `ARM64` configuration.

## Architecture

### Local AI Architecture Flow

```mermaid
flowchart TD
    User[Presenter or attendee] --> UI[WinUI 3 app: {spec.title}]
    UI --> Runtime[LocalAiRuntime]
    Runtime --> SDK[Foundry Local SDK, in-process]
    SDK --> Primary[Pinned primary model on the NPU]
    Primary -->|load fails| Backup[Pinned backup model on the NPU]
    Backup -->|load fails| Consent{{Presenter consents to GPU?}}
    Consent -->|yes| GPU[GPU, still on-device]
    Consent -->|no| Replay[Labelled replay of outputs recorded on this NPU]
    Runtime --> Watchdog[First-token watchdog]
    Watchdog -->|stall| Replay
    Runtime --> Telemetry[NPU counters, tokens/s, time to first token]
    Telemetry --> UI
```

The app loads `{p.model_id if p else 'the selected model'}` in-process through the Foundry Local
SDK and runs it on the NPU. There are no REST calls to cloud AI endpoints. The right-hand
**Local Inference** panel shows the exact model build, the device it runs on, live NPU
utilisation (read from the same Windows counters Task Manager uses), tokens per second and
time to first token.

## Model Requirements

| Role | Alias | Pinned build | Device | Size | License |
|---|---|---|---|---|---|
{primary_row}
{backup_row}

### Why this model

{reasons}

Models are pinned by exact build (including version) in `src/{app}/Assets/app-config.json`
so the demo cannot change underneath you.

## GPU Fallback Policy

NPU first, always. If both NPU models fail to load, the presenter is asked before the GPU is
used (`GpuFallbackConsentService`, wired to a ContentDialog). Declining keeps the demo on the
labelled replay safety net. The GPU is never used silently.

## Demo Script

See [docs/DEMO-RUNBOOK.md](docs/DEMO-RUNBOOK.md) for the full stage runbook. Sample prompts:

{samples}

Presenter shortcuts: `F5` run, `Esc` stop, `Ctrl+1..3` samples, `Ctrl+R` replay safety net, `F11` full screen.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Status says "Optimising for the NPU" | First-run compile. Run the harness preflight before the session. |
| Badge shows REPLAY | The live model is unavailable. Outputs shown were recorded on this device and are labelled. Check `%LOCALAPPDATA%\\{app}\\logs`. |
| NPU shows "counters unavailable" | Windows GPU Engine counters are blocked. Inference still runs on the NPU; show Task Manager > Performance > NPU instead. |
| Model not found | Run `foundry model download {p.model_id if p else '<model>'}` while online. |

## Disclaimer

This application is a **prototype created for educational and demonstration purposes** by the
Microsoft Surface AI Factory team. Not for production use. AI outputs may be inaccurate.
Zava is a fictitious company. All AI inference is performed locally on-device.
"""


def runbook(spec: AppSpec, plan: ModelPlan, app: str) -> str:
    p, b = plan.primary, plan.backup
    samples = "\n".join(f"| Ctrl+{i} | {s} |" for i, s in enumerate(spec.sample_prompts, 1))
    return f"""# Demo runbook: {spec.title}

Zava | {WATERMARK_TEXT}

## The day before

- [ ] Device on mains power, Windows Update paused, notifications off (Focus).
- [ ] `python harness.py --preflight <this folder>` shows **GO FOR SHOW**.
- [ ] Rehearse once in airplane mode: the network chip turns green ("OFFLINE, AI STILL RUNNING").

## 30 minutes before

- [ ] Run preflight again (it re-warms the NPU cache in seconds).
- [ ] `tools\\Start-Demo.ps1`, wait for the badge to show **NPU** and status **Live**.
- [ ] Open Task Manager > Performance > NPU on the second screen (optional proof point).

## On stage

1. Say what the app is for: *{spec.tagline}*
2. Point at the **Local Inference** panel: model `{p.alias if p else '-'}`, build `{p.model_id if p else '-'}`, running on the **NPU**.
3. Read one line from **Why this model**.
4. Run the samples:

| Shortcut | Prompt |
|---|---|
{samples}

5. While it streams, call out tokens/sec, time to first token and the NPU utilisation graph.
6. Optional wow moment: switch on airplane mode and run again.

## If something goes wrong

| What you see | What to do | What to say |
|---|---|---|
| Status stuck on "Optimising" | Press `Ctrl+R` (replay) and carry on | "This is a recording from this same NPU earlier today, labelled as replay." |
| Badge says backup model | Carry on | "Our fallback model `{b.alias if b else '-'}` is also on the NPU." |
| GPU consent dialog | Choose **No, stay NPU-only** | "We never move to the GPU without asking." |
| App frozen | `Esc`, then relaunch with `tools\\Start-Demo.ps1` | "Live demos, everyone." |

Replay is always labelled REPLAY on screen. Never present a replay as live.
"""


GITIGNORE = """bin/
obj/
.vs/
*.user
*.suo
TestResults/
models/*.onnx
models/*.bin
*.env
"""


# ── File helpers ────────────────────────────────────────────────────────────

def _copy_tree(src: Path, dest: Path, tokens: dict[str, str]) -> None:
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        rel = str(item.relative_to(src))
        for key, value in tokens.items():
            rel = rel.replace(key, value)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if item.suffix.lower() in TEXT_SUFFIXES:
            text = _read(item)
            for key, value in tokens.items():
                text = text.replace(key, value)
            _write(target, text)
        else:
            shutil.copyfile(item, target)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _force_remove(func, path, _exc):
    import os
    import stat
    os.chmod(path, stat.S_IWRITE)
    func(path)


def slug(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return s[:60] or "zava-local-app"
