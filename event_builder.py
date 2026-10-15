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
    knowledge_docs = _copy_knowledge(spec, project_dir)

    meta = root / ".surface-ai-factory"
    _write(meta / "model-plan.json", json.dumps(plan.to_dict(), indent=2))
    _write(meta / "app-spec.json", json.dumps(spec.to_dict(), indent=2))

    _write(root / "models" / "model_catalog.json", json.dumps(model_catalog(plan), indent=2))
    _write(root / "README.md", readme(spec, plan, repo_name, app, knowledge_docs))
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


KNOWLEDGE_PACKS = TEMPLATES / "knowledge"


def _copy_knowledge(spec: AppSpec, project_dir: Path) -> list[str]:
    """Ship the industry's sample knowledge base (Markdown, one passage per ## section)."""
    target = project_dir / "Assets" / "knowledge"
    pack = KNOWLEDGE_PACKS / spec.knowledge_pack if spec.knowledge_pack else None
    docs: list[str] = []
    if pack and pack.is_dir():
        for src in sorted(pack.rglob("*.md")):
            dest = target / src.relative_to(pack)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            docs.append(str(src.relative_to(pack)).replace("\\", "/"))
    elif spec.knowledge:
        _write(target / "public" / "README.txt",
               "Add Markdown files here (one topic per '## ' heading). They are searched on this device "
               "and used to ground answers. Files in ../staff are only used by staff modes.\n")
    return docs


def app_config(spec: AppSpec, plan: ModelPlan) -> dict:
    return {
        "appTitle": spec.title,
        "tagline": spec.tagline,
        "idea": plan.analysis.idea,
        "industry": spec.industry,
        "organisation": spec.organisation,
        "theme": spec.theme,
        "modes": spec.modes,
        "contextTitle": spec.context_title,
        "contextFields": spec.context_fields,
        "localDrivers": spec.local_drivers,
        "brand": spec.brand,
        "languages": spec.languages,
        "systemPrompt": spec.system_prompt,
        "inputLabel": spec.input_label,
        "samplePrompts": spec.sample_prompts,
        "primary": _model_ref(plan.primary),
        "backup": _model_ref(plan.backup),
        "selectionReasons": plan.primary.reasons if plan.primary else [],
        "firstTokenTimeoutSeconds": 45,
        "maxTokens": 400,
        "temperature": 0.1,
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

def _modes_table(spec: AppSpec) -> str:
    rows = [f"| **{m['name']}**{f' (F{i + 2})' if len(spec.modes) > 1 else ''} | {m['inputLabel']} | {m['samplePrompts'][0] if m['samplePrompts'] else ''} |"
            for i, m in enumerate(spec.modes)]
    return "| Mode | Asks | For example |\n|---|---|---|\n" + "\n".join(rows)


def readme(spec: AppSpec, plan: ModelPlan, repo_name: str, app: str, knowledge_docs: list[str] | None = None) -> str:
    p, b = plan.primary, plan.backup
    knowledge_docs = knowledge_docs or []
    drivers = "\n".join(f"- **{d['title']}.** {d['detail']}" for d in spec.local_drivers)
    context = ""
    if spec.context_fields:
        fields = ", ".join(f["label"].lower() for f in spec.context_fields)
        context = (f"\n**{spec.context_title or 'Context'}:** {fields}. These details shape the answer but are held "
                   "in memory only: never logged, saved or sent. `Ctrl+L` clears them for the next person.\n")
    knowledge = ""
    if spec.knowledge:
        listed = "\n".join(f"- `{d}`" for d in knowledge_docs) or "- (none yet: add Markdown files)"
        knowledge = f"""
## What it knows

Answers are grounded in {spec.organisation}'s own documents in `src/{app}/Assets/knowledge`, searched on
this device (BM25, in memory, no cloud index). Every answer lists the passages it used. Update the
knowledge by editing the files; no retraining. Files under `staff/` are only used by staff modes.

{listed}
"""
    samples = "\n".join(
        f"| {m['name']} | " + " | ".join(m["samplePrompts"][:3]) + " |" for m in spec.modes)
    gaps = "".join(f"\n> **Capability note:** {g.detail} Best available: {g.best_available or 'none'}.\n"
                   for g in plan.gaps)
    return f"""<img src="Assets/zava-logo.png" alt="Zava" height="32" />

# {spec.title}

{spec.tagline}.

> **{WATERMARK_TEXT}**, a Microsoft Surface engineering prototype for Zava ({spec.industry_name}).
> All AI runs locally on this device. No data leaves your device.
{gaps}
## What it does

{_modes_table(spec)}
{context}
## Why it runs on this device

{drivers}
{knowledge}
## Run it

The app is built and run on this device only. It is not packaged or published.

1. `python harness.py --preflight {repo_name}` (from the harness folder) builds it, prepares the model and
   rehearses each mode.
2. `powershell -File tools\\Start-Demo.ps1`

Shortcuts: {"`F2`-`F4` mode, " if len(spec.modes) > 1 else ""}`F5` run, `Esc` stop, `Ctrl+1..3` suggestions,
`Ctrl+L` clear, `Ctrl+I` details, `Ctrl+R` replay safety net, `F11` full screen.

## Demo samples

| Mode | Ctrl+1 | Ctrl+2 | Ctrl+3 |
|---|---|---|---|
{samples}

See [docs/DEMO-RUNBOOK.md](docs/DEMO-RUNBOOK.md) for the stage script.

## Under the hood

```mermaid
flowchart LR
    User[User] --> UI[{spec.title}]
    UI --> Engine[RequestEngine: private context + local knowledge search]
    Engine --> Runtime[LocalAiRuntime]
    Runtime --> SDK[Foundry Local SDK, in-process]
    SDK --> Model[Pinned local model]
    Model -->|load fails| Backup[Pinned backup model]
    Backup -->|load fails| Consent{{GPU only with consent}}
    Consent -->|no| Replay[Labelled replay recorded on this device]
```

| Role | Model | Pinned build | Runs on | Size | Licence |
|---|---|---|---|---|---|
| Primary | `{p.alias if p else '-'}` | `{p.model_id if p else '-'}` | {p.device if p else '-'} | {(p.size_mb / 1024) if p else 0:.2f} GB | {p.license if p else '-'} |
| Backup | `{b.alias if b else '-'}` | `{b.model_id if b else '-'}` | {b.device if b else '-'} | {(b.size_mb / 1024) if b else 0:.2f} GB | {b.license if b else '-'} |

Model, device and speed are shown in the ribbon at the bottom of the app; `Ctrl+I` opens the details
drawer (build, execution provider, live NPU graph, tokens per second, time to first token). Models and
the compiled model cache are shared by every Zava Live Build app on this device
(`%LOCALAPPDATA%\\ZavaLiveBuild`), so each model is prepared once per device. The GPU is never used
without the presenter's consent; failures fall back to replays recorded on this device, always
labelled REPLAY.

Configuration (theme, modes, prompts, samples, context fields, model pins): `src/{app}/Assets/app-config.json`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Ribbon says "Preparing ... first time only" | The model is being compiled for this device. Run the preflight before the session. |
| Answer badge says REPLAY | The live model is unavailable. Check `%LOCALAPPDATA%\\{app}\\logs`. |
| Model not found | `foundry model download {p.model_id if p else '<model>'}` while online. |

## Disclaimer

Prototype for educational and demonstration purposes by the Microsoft Surface AI Factory team. Not for
production use. AI output may be inaccurate. Zava and {spec.organisation} are fictitious. All AI inference
is performed locally on this device.
"""


def runbook(spec: AppSpec, plan: ModelPlan, app: str) -> str:
    p, b = plan.primary, plan.backup
    rows = "\n".join(
        f"| {m['name']}{f' (F{i + 2})' if len(spec.modes) > 1 else ''} | " + " | ".join(m["samplePrompts"][:3]) + " |"
        for i, m in enumerate(spec.modes))
    drivers = " ".join(d["title"].rstrip(".") + "." for d in spec.local_drivers)
    first_driver = spec.local_drivers[0]["detail"] if spec.local_drivers else "Everything runs on this device."
    context_step = ""
    if spec.context_fields:
        example = "; ".join(f"{f['label']}: {f['placeholder'].replace('e.g. ', '')}" for f in spec.context_fields)
        context_step = (f"\n3. **The private moment.** Fill in {spec.context_title or 'the details'} ({example}) and run "
                        f"again. Point out that the answer changed, and say: *\"{first_driver}\"* Press `Ctrl+L`: "
                        "it is gone for the next person.")
    later = len(spec.modes) > 1
    return f"""# Demo runbook: {spec.title}

Zava | {WATERMARK_TEXT}

## Before the session

- [ ] Device on mains power, notifications off (Focus).
- [ ] `python harness.py --preflight {slug(spec.title)}` shows **GO FOR SHOW**.
- [ ] Optional: rehearse once in airplane mode; the header shows "Offline, still working".
- [ ] `tools\\Start-Demo.ps1`; wait until the ribbon at the bottom shows the model is ready.

## On stage

**Lead with the job, not the hardware.** The story: *{spec.tagline}.* {drivers}

1. Introduce {spec.organisation} and who uses the app. Point at **Why this runs on this device** on the right.
2. **{spec.modes[0]['name']}:** `Ctrl+1`, then `F5`. Let the answer stream.{' Point at the sources under the answer: grounded in their own documents, searched on this PC.' if spec.knowledge else ''}{context_step}
{"4. Switch modes with `F3`/`F4` and run `Ctrl+1` in each: same app, same device, a different job." if later else ""}
5. Optional, for technical audiences: `Ctrl+I` opens the details drawer (model, device, speed). Otherwise
   the ribbon at the bottom is enough.
6. Optional wow moment: airplane mode on, run again.

| Mode | Ctrl+1 | Ctrl+2 | Ctrl+3 |
|---|---|---|---|
{rows}

## If something goes wrong

| What you see | What to do | What to say |
|---|---|---|
| Ribbon still "Preparing" | `Ctrl+R` (replay) and carry on | "This is an answer recorded on this same PC earlier, labelled as a replay." |
| Ribbon says backup | Carry on | "A smaller backup model `{b.alias if b else '-'}` is answering, still on this device." |
| GPU consent dialog | Choose **No, stay NPU-only** | "We never move work without asking." |
| App frozen | `Esc`, relaunch with `tools\\Start-Demo.ps1` | "Live demos, everyone." |

Replays are always labelled REPLAY on screen. Never present a replay as live.
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
