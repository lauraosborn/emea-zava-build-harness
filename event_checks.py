"""
Zava Live Build -- Section 11: Live Event Readiness.

Extra checks that sit alongside the ten Surface AI Factory sections. They
verify the things that make a live local-AI demo bulletproof rather than
merely compliant.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from best_practices import FAIL, PASS, WARN, Finding, SectionResult, _find_files, _read

SECTION = "11. Live Event Readiness"


def _app_config(root: Path) -> tuple[Path | None, dict]:
    for path in root.glob("src/*/Assets/app-config.json"):
        try:
            return path, json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return path, {}
    return None, {}


def check_event_readiness(root: Path) -> SectionResult:
    result = SectionResult(SECTION)
    add = result.findings.append
    cs = _find_files(root, ".cs")
    xaml = _find_files(root, ".xaml")
    cs_text = "\n".join(_read(f) for f in cs)
    xaml_text = "\n".join(_read(f) for f in xaml)

    path, config = _app_config(root)
    primary = (config.get("primary") or {}) if config else {}
    if primary.get("modelId") and ":" in primary["modelId"]:
        add(Finding(PASS, "model-pinned", f"Primary model pinned to exact build {primary['modelId']}.", str(path)))
    else:
        add(Finding(FAIL, "model-pinned", "Primary model is not pinned to an exact build (alias:version).",
                    str(path or ""), "Pin the Foundry Local model id including its version in app-config.json."))

    if "npu" in (primary.get("modelId", "").lower()):
        add(Finding(PASS, "npu-model", "Pinned primary model is an NPU build."))
    else:
        add(Finding(FAIL, "npu-model", "Pinned primary model is not an NPU build.",
                    fix="Re-run model selection; only NPU builds are allowed as the stage primary."))

    if config.get("backup"):
        add(Finding(PASS, "backup-model", f"Stage backup model: {config['backup'].get('modelId')}."))
    else:
        add(Finding(WARN, "backup-model", "No backup NPU model configured.",
                    fix="Add a small cached NPU model as 'backup' in app-config.json."))

    if re.search(r"--warmup", cs_text):
        add(Finding(PASS, "warmup-mode", "App supports headless --warmup to precompile the NPU model."))
    else:
        add(Finding(FAIL, "warmup-mode", "No --warmup mode: the NPU compile would happen on stage.",
                    fix="Add a headless warm-up entry point (see WarmupRunner.cs)."))

    if re.search(r"FirstTokenTimeout|watchdog", cs_text, re.IGNORECASE):
        add(Finding(PASS, "first-token-watchdog", "First-token watchdog prevents on-stage hangs."))
    else:
        add(Finding(FAIL, "first-token-watchdog", "No first-token watchdog: a stalled model would hang the demo."))

    if re.search(r"ReplayLibrary", cs_text) and re.search(r"REPLAY", cs_text + xaml_text):
        add(Finding(PASS, "labelled-replay", "Replay safety net exists and is labelled REPLAY in the UI."))
    else:
        add(Finding(FAIL, "labelled-replay", "No labelled replay safety net.",
                    fix="Record outputs during warm-up and show them with a visible REPLAY badge."))

    samples = config.get("samplePrompts") or []
    if len(samples) >= 3:
        add(Finding(PASS, "sample-prompts", f"{len(samples)} rehearsed sample prompts with shortcuts."))
    else:
        add(Finding(WARN, "sample-prompts", "Fewer than 3 sample prompts configured."))

    panel_markers = ("DeviceBadge", "NpuSparkline", "FirstTokenText", "TokenProgressText", "ModelIdText")
    missing = [m for m in panel_markers if m not in xaml_text]
    if not missing:
        add(Finding(PASS, "inference-details", "Model, device, NPU graph, tok/s and TTFT available in the details drawer."))
    else:
        add(Finding(WARN, "inference-details", f"Local inference details are missing: {', '.join(missing)}."))

    # App first: the screen is about the job the app does, not the hardware.
    hardware = re.compile(r"\bNPU\b", re.IGNORECASE)
    user_facing = [config.get("appTitle", ""), config.get("tagline", ""), config.get("inputLabel", "")]
    for mode in config.get("modes") or []:
        user_facing += [mode.get("name", ""), mode.get("inputLabel", ""), mode.get("action", ""),
                        mode.get("outputTitle", "")]
    leaks = [t for t in user_facing if t and hardware.search(t)]
    if leaks:
        add(Finding(FAIL, "app-first-wording",
                    f"Hardware talk in the app's own wording: {leaks[0][:60]!r}.", str(path or ""),
                    "Describe what the app does for its users; keep model and device details in the ribbon."))
    else:
        add(Finding(PASS, "app-first-wording", "Title, tagline, modes and actions describe the job, not the hardware."))

    theme = config.get("theme") or {}
    if theme.get("accent") and config.get("industry"):
        add(Finding(PASS, "industry-design",
                    f"{config['industry']} theme (accent {theme['accent']}, {theme.get('displayFont', 'default font')})."))
    else:
        add(Finding(WARN, "industry-design", "No industry theme in app-config.json: the app uses the default look.",
                    fix="Regenerate with the current harness so the theme follows the idea's industry."))

    if "LocalDriversPanel" in xaml_text and config.get("localDrivers"):
        add(Finding(PASS, "local-ai-story",
                    "Explains why it runs locally: " + "; ".join(d.get("title", "") for d in config["localDrivers"]) + "."))
    else:
        add(Finding(WARN, "local-ai-story", "The app does not explain why it runs on the device.",
                    fix="Add localDrivers to app-config.json and show them in the main window."))

    if "RibbonStatusText" in xaml_text and "DetailsDrawer" in xaml_text:
        add(Finding(PASS, "secondary-tech-details", "Model and device details sit in the bottom ribbon and details drawer."))
    else:
        add(Finding(WARN, "secondary-tech-details", "Model and device details are not confined to a ribbon or drawer."))

    from event_flow import is_cloud_synced
    if is_cloud_synced(root.resolve()):
        add(Finding(WARN, "local-storage", f"App folder is in a cloud-synced location: {root}.",
                    fix="Generate into %USERPROFILE%\\ZavaLiveBuilds (the default)."))
    else:
        add(Finding(PASS, "local-storage", f"Stored on this device: {root.resolve()}."))

    if re.search(r"NetworkStatusChanged|GetInternetConnectionProfile", cs_text):
        add(Finding(PASS, "offline-indicator", "Network state chip supports the airplane-mode moment."))
    else:
        add(Finding(WARN, "offline-indicator", "No on-screen network indicator for the airplane-mode demo."))

    if re.search(r"KeyboardAccelerator", xaml_text) and re.search(r'Key="F11"', xaml_text):
        add(Finding(PASS, "presenter-shortcuts", "Presenter shortcuts (run, stop, samples, replay, full screen)."))
    else:
        add(Finding(WARN, "presenter-shortcuts", "No presenter keyboard shortcuts."))

    preflight = root / ".surface-ai-factory" / "preflight-report.json"
    if preflight.exists():
        try:
            go = json.loads(preflight.read_text(encoding="utf-8")).get("go")
        except json.JSONDecodeError:
            go = False
        add(Finding(PASS if go else FAIL, "preflight",
                    "Last preflight: GO FOR SHOW." if go else "Last preflight was NO-GO.",
                    str(preflight), "" if go else "Fix the failed items and re-run --preflight."))
    else:
        add(Finding(WARN, "preflight", "Preflight has not been run on this device yet.",
                    fix="python harness.py --preflight <app folder>"))

    if (root / "docs" / "DEMO-RUNBOOK.md").exists():
        add(Finding(PASS, "runbook", "Stage runbook with recovery playbook present."))
    else:
        add(Finding(WARN, "runbook", "No docs/DEMO-RUNBOOK.md stage runbook."))

    return result
