"""
Surface AI Factory — Best Practices compliance checker.
Each check returns a ComplianceResult with status, findings, and recommended fixes.
"""

from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


@dataclass
class Finding:
    severity: str          # PASS / WARN / FAIL
    rule: str
    message: str
    file: str = ""
    fix: str = ""          # human-readable fix description
    auto_fix: bool = False  # can this be applied automatically?


@dataclass
class SectionResult:
    section: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(f.severity == FAIL for f in self.findings):
            return FAIL
        if any(f.severity == WARN for f in self.findings):
            return WARN
        return PASS


# ─── helpers ─────────────────────────────────────────────────────────────────

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _find_files(root: Path, *exts) -> list[Path]:
    result = []
    for ext in exts:
        result.extend(root.rglob(f"*{ext}"))
    # skip .git, node_modules, bin, obj, and the harness's own reports (they quote rule text)
    return [f for f in result if not any(p in f.parts for p in (".git", "node_modules", "bin", "obj", "__pycache__", ".surface-ai-factory"))]


def _any_contains(files: list[Path], pattern: str, flags=re.IGNORECASE) -> list[Path]:
    return [f for f in files if re.search(pattern, _read(f), flags)]


def _none_contains(files: list[Path], pattern: str, flags=re.IGNORECASE) -> bool:
    return not _any_contains(files, pattern, flags)


# ─── Section 1: Branding & UX ────────────────────────────────────────────────
#
# P0 guardrails. The document requires TWO distinct things:
#   1.1  Zava company branding (name + logo) on all applications
#   1.1  The exact watermark "Built by Surface AI Factory", attributed in
#        FOUR named surfaces: main application UI, About page, README, installer
#   1.2  AI transparency: model in use, local AI status, NPU utilisation,
#        token generation progress
#
# Each surface is checked independently — a single mention somewhere in the
# repo does NOT satisfy the requirement.

# Exact mandated watermark text (Section 1.1). Tolerates whitespace variation
# only; any other wording is a failure.
WATERMARK_TEXT = "Built by Surface AI Factory"
WATERMARK_RE = r"built\s+by\s+surface\s+ai\s+factory"
ZAVA_RE = r"\bzava\b"

# Files that constitute the "main application UI" surface.
UI_EXTS = (".xaml", ".html", ".razor", ".axaml")

# Installer / packaging surfaces.
INSTALLER_GLOBS = (
    "**/Package.appxmanifest", "**/*.wxs", "**/*.wixproj", "**/*.iss",
    "**/*.nuspec", "**/setup.iss", "**/*.msix*", "**/installer/**/*",
)


CONSOLE_ENTRYPOINTS = (
    "main.py", "app.py", "__main__.py", "cli.py", "run.py",
    "branding.py", "Program.cs", "index.js", "main.js", "main.ts",
)


def _ui_files(root: Path) -> list[Path]:
    return _find_files(root, *UI_EXTS)


def _console_ui_files(root: Path) -> list[Path]:
    """
    Fallback 'presentation surface' for repos with no GUI framework.
    A CLI still has a main application UI — its startup output — so the
    watermark and Zava branding must live in the entrypoint or a dedicated
    branding module that the entrypoint renders.
    """
    return [
        f for f in _find_files(root, ".py", ".cs", ".js", ".ts")
        if f.name in CONSOLE_ENTRYPOINTS
    ]


def _main_ui_files(root: Path) -> list[Path]:
    """The primary shell/window files — where the watermark must be visible."""
    ui = _ui_files(root)
    preferred = [f for f in ui if f.name in (
        "MainWindow.xaml", "MainPage.xaml", "Shell.xaml", "App.xaml",
        "index.html", "MainWindow.axaml",
    )]
    return preferred or ui or _console_ui_files(root)


def _about_files(root: Path) -> list[Path]:
    """Any file that looks like an About page/screen/dialog."""
    hits = []
    for f in _find_files(root, *UI_EXTS, ".cs", ".md"):
        if re.search(r"about", f.name, re.IGNORECASE):
            hits.append(f)
    return hits


def _installer_files(root: Path) -> list[Path]:
    hits: list[Path] = []
    for pattern in INSTALLER_GLOBS:
        for f in root.glob(pattern):
            if f.is_file() and not any(p in f.parts for p in (".git", "node_modules", "bin", "obj")):
                hits.append(f)
    return list(dict.fromkeys(hits))


def check_branding(root: Path) -> SectionResult:
    r = SectionResult("1. Branding & User Experience")

    readme_files = list(root.glob("README*")) + list(root.glob("readme*"))
    ui_files = _ui_files(root) or _console_ui_files(root)
    main_ui = _main_ui_files(root)
    about_files = _about_files(root)
    installer_files = _installer_files(root)
    brandable = _find_files(root, *UI_EXTS, ".cs", ".md", ".json", ".appxmanifest")

    # ── 1.1 Zava company branding ────────────────────────────────────────────
    zava_hits = _any_contains(brandable, ZAVA_RE)
    if zava_hits:
        r.findings.append(Finding(
            PASS, "zava-branding",
            f"Zava company branding found in {len(zava_hits)} file(s)."
        ))
    else:
        r.findings.append(Finding(
            FAIL, "zava-branding",
            "No Zava company branding found anywhere in the repository.",
            fix="Add Zava as the company name via BrandingConstants, the app UI, "
                "the About page, the README header, and the package manifest.",
            auto_fix=True
        ))

    # Zava branding must reach the actual UI, not just docs.
    if ui_files:
        if _any_contains(ui_files, ZAVA_RE):
            r.findings.append(Finding(PASS, "zava-branding-ui", "Zava branding present in application UI."))
        else:
            r.findings.append(Finding(
                FAIL, "zava-branding-ui",
                "Zava branding is missing from the application UI surfaces.",
                fix="Display the Zava company name (and logo) in the main window and About page.",
                auto_fix=True
            ))

    # Zava logo asset
    logo_assets = [
        f for f in root.rglob("*")
        if f.is_file()
        and re.search(r"zava", f.name, re.IGNORECASE)
        and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".svg", ".ico")
        and ".git" not in f.parts
    ]
    if logo_assets:
        r.findings.append(Finding(
            PASS, "zava-logo",
            f"Zava logo asset found ({logo_assets[0].name})."
        ))
    else:
        r.findings.append(Finding(
            WARN, "zava-logo",
            "No Zava logo asset found — the document requires the logo alongside the company name.",
            fix="Add Assets/zava-logo.png and reference it from the main UI and About page.",
            auto_fix=True
        ))

    # ── 1.1 Watermark: exact text, in each of the four required surfaces ─────

    # (a) Main application UI
    if main_ui:
        if _any_contains(main_ui, WATERMARK_RE):
            r.findings.append(Finding(PASS, "watermark-app-ui", "Watermark present in main application UI."))
        else:
            r.findings.append(Finding(
                FAIL, "watermark-app-ui",
                f"Main application UI is missing the '{WATERMARK_TEXT}' watermark.",
                file=str(main_ui[0].relative_to(root)),
                fix=f"Render '{WATERMARK_TEXT}' persistently in the main window.",
                auto_fix=True
            ))
    else:
        r.findings.append(Finding(
            FAIL, "watermark-app-ui",
            "No application UI surface found to carry the watermark.",
            fix="This repo has no XAML/HTML UI. The harness will ask how to proceed.",
            auto_fix=False
        ))

    # (b) About page
    if about_files and _any_contains(about_files, WATERMARK_RE):
        r.findings.append(Finding(PASS, "watermark-about", "About page carries the watermark."))
    elif about_files:
        r.findings.append(Finding(
            FAIL, "watermark-about",
            f"An About page exists but is missing the '{WATERMARK_TEXT}' watermark.",
            file=str(about_files[0].relative_to(root)),
            fix="Add the watermark and Zava branding to the About page.",
            auto_fix=True
        ))
    else:
        r.findings.append(Finding(
            FAIL, "watermark-about",
            "No About page found — attribution on an About page is required.",
            fix="Scaffold an About page carrying Zava branding and the watermark.",
            auto_fix=True
        ))

    # (c) README
    if readme_files:
        readme_text = _read(readme_files[0])
        if re.search(WATERMARK_RE, readme_text, re.IGNORECASE):
            r.findings.append(Finding(PASS, "watermark-readme", "README carries the watermark."))
        else:
            r.findings.append(Finding(
                FAIL, "watermark-readme",
                f"README is missing the '{WATERMARK_TEXT}' attribution.",
                file=str(readme_files[0].relative_to(root)),
                fix=f"Add '> **{WATERMARK_TEXT}**' near the top of the README.",
                auto_fix=True
            ))
    else:
        r.findings.append(Finding(
            FAIL, "watermark-readme",
            "No README found — README attribution is required.",
            fix="Create a README carrying Zava branding and the watermark.",
            auto_fix=True
        ))

    # (d) Installer
    if installer_files:
        if _any_contains(installer_files, WATERMARK_RE) or _any_contains(installer_files, ZAVA_RE):
            r.findings.append(Finding(PASS, "watermark-installer", "Installer/package metadata carries branding."))
        else:
            r.findings.append(Finding(
                FAIL, "watermark-installer",
                "Installer/package manifest is missing Zava branding and attribution.",
                file=str(installer_files[0].relative_to(root)),
                fix="Set PublisherDisplayName to Zava and include the Surface AI Factory attribution.",
                auto_fix=True
            ))
    else:
        r.findings.append(Finding(
            WARN, "watermark-installer",
            "No installer or package manifest found to carry attribution.",
            fix="Add a Package.appxmanifest (MSIX) with Zava as publisher.",
            auto_fix=False
        ))

    # ── 1.2 AI transparency: all four indicators ─────────────────────────────
    indicators = {
        "model in use":              r"model\s*(name|in use)?\s*[:=]|ModelNameText|modelName",
        "local AI status":           r"local\s*ai|on-?device|LocalAiText|LocalAiStatus",
        "NPU utilisation":           r"npu",
        "token generation progress": r"tok/s|tokens?\s*per\s*sec|TokenSpeed|tokensPerSecond|token.*progress",
    }
    transparency_surfaces = ui_files + _find_files(root, ".cs")
    if transparency_surfaces:
        missing = [
            label for label, pattern in indicators.items()
            if not _any_contains(transparency_surfaces, pattern)
        ]
        if not missing:
            r.findings.append(Finding(
                PASS, "ai-transparency",
                "All four AI transparency indicators present (model, local AI status, NPU, token progress)."
            ))
        else:
            r.findings.append(Finding(
                FAIL if len(missing) > 2 else WARN, "ai-transparency",
                f"AI transparency indicators missing: {', '.join(missing)}.",
                fix="Add an NPU status bar showing model in use, local AI status, "
                    "NPU utilisation, and token generation progress.",
                auto_fix=True
            ))

    return r


# ─── Section 2: Application Architecture ─────────────────────────────────────

def check_architecture(root: Path) -> SectionResult:
    r = SectionResult("2. Application Architecture")

    all_files = _find_files(root, ".py", ".js", ".ts", ".cs", ".csproj", ".xaml")
    py_files = _find_files(root, ".py")
    js_files = _find_files(root, ".js", ".ts")
    csproj_files = _find_files(root, ".csproj")
    xaml_files = _find_files(root, ".xaml")

    # Python usage
    entry_py = [f for f in py_files if f.name not in ("setup.py", "conf.py")]
    if entry_py:
        r.findings.append(Finding(
            FAIL, "no-python",
            f"Python files detected ({len(entry_py)} files). Best practices require WinUI3/Windows App SDK — avoid Python.",
            file=str(entry_py[0].relative_to(root)),
            fix="Migrate application logic to C#/.NET with WinUI3 and Windows App SDK."
        ))
    else:
        r.findings.append(Finding(PASS, "no-python", "No Python application files detected."))

    # Electron/Node.js usage
    electron_files = [f for f in js_files if re.search(r"electron|renderer\.js|main\.js", f.name, re.IGNORECASE)]
    package_json = root / "package.json"
    if electron_files or (package_json.exists() and re.search(r"electron", _read(package_json), re.IGNORECASE)):
        r.findings.append(Finding(
            FAIL, "no-electron",
            "Electron/Node.js usage detected. Best practices require WinUI3, not Electron.",
            fix="Migrate to WinUI3 / Windows App SDK."
        ))
    else:
        r.findings.append(Finding(PASS, "no-electron", "No Electron usage detected."))

    # WinUI3 / Windows App SDK
    if csproj_files:
        csproj_text = " ".join(_read(f) for f in csproj_files)
        if re.search(r"Microsoft\.WindowsAppSDK|WinUI|WindowsAppSdk", csproj_text, re.IGNORECASE):
            r.findings.append(Finding(PASS, "winui3", "Windows App SDK / WinUI3 reference found in project file."))
        else:
            r.findings.append(Finding(
                FAIL, "winui3",
                "No Windows App SDK / WinUI3 reference found in .csproj.",
                fix="Add <PackageReference Include=\"Microsoft.WindowsAppSDK\" /> to .csproj.",
                auto_fix=True
            ))
    elif not xaml_files:
        r.findings.append(Finding(
            WARN, "winui3",
            "No .csproj or .xaml files found. Cannot verify WinUI3 architecture.",
            fix="Ensure application is built with WinUI3 and Windows App SDK."
        ))

    # Offline-first check
    cloud_patterns = r"http[s]?://(?!localhost|127\.0\.0\.1)[a-z].*\.openai\.com|azure\.openai|cognitiveservices\.azure"
    if _any_contains(all_files, cloud_patterns):
        r.findings.append(Finding(
            FAIL, "offline-first",
            "Cloud AI endpoint detected. Applications must be offline-first with local AI primary.",
            fix="Replace cloud AI calls with Foundry Local SDK (see Section 3)."
        ))
    else:
        r.findings.append(Finding(PASS, "offline-first", "No cloud AI endpoints detected — offline-first pattern confirmed."))

    return r


# ─── Section 3: Inference Framework ──────────────────────────────────────────

def check_inference(root: Path) -> SectionResult:
    r = SectionResult("3. Inference Framework Standards")

    cs_files = _find_files(root, ".cs")
    all_code = _find_files(root, ".cs", ".py", ".js", ".ts")

    # Direct REST to AI endpoints
    direct_rest = r'new HttpClient.*chat/completions|HttpRequestMessage.*completions|fetch\(.*completions'
    rest_hits = _any_contains(all_code, direct_rest)
    if rest_hits:
        r.findings.append(Finding(
            FAIL, "no-direct-rest",
            f"Direct REST calls to AI completion endpoints detected ({len(rest_hits)} files). Use Foundry Local SDK instead.",
            file=str(rest_hits[0].relative_to(root)),
            fix="Replace direct HttpClient calls with FoundryLocalClient or model abstraction service.",
            auto_fix=True
        ))
    else:
        r.findings.append(Finding(PASS, "no-direct-rest", "No direct REST AI calls detected."))

    # Foundry Local SDK
    foundry_pattern = r"FoundryLocal|foundry[_\-]local|Microsoft\.AI\.Foundry"
    if _any_contains(cs_files, foundry_pattern):
        r.findings.append(Finding(PASS, "foundry-sdk", "Foundry Local SDK usage detected."))
    elif cs_files:
        r.findings.append(Finding(
            WARN, "foundry-sdk",
            "Foundry Local SDK not detected. Prefer FoundryLocalClient over raw HTTP calls.",
            fix="Add FoundryLocal NuGet package and use FoundryLocalClient for model inference.",
            auto_fix=True
        ))

    # Model abstraction layer
    abstraction_pattern = r"IInferenceService|IModelService|InferenceService|ModelService|ILanguageModel"
    if _any_contains(cs_files, abstraction_pattern):
        r.findings.append(Finding(PASS, "model-abstraction", "Model abstraction layer detected."))
    elif cs_files:
        r.findings.append(Finding(
            WARN, "model-abstraction",
            "No model abstraction interface detected. Add IInferenceService to support future model swaps.",
            fix="Add IInferenceService interface and implementation to decouple inference from app logic.",
            auto_fix=True
        ))

    # Silicon agnostic — no vendor lock
    vendor_lock = r"intel_npu_only|qualcomm_only|qnn_only|openvino_only"
    if _any_contains(all_code, vendor_lock, re.IGNORECASE):
        r.findings.append(Finding(
            WARN, "silicon-agnostic",
            "Vendor-specific inference paths detected. App should be silicon-agnostic (Intel/Qualcomm/AMD).",
            fix="Abstract hardware selection behind a runtime detection layer."
        ))
    else:
        r.findings.append(Finding(PASS, "silicon-agnostic", "No vendor-specific inference lock detected."))

    # GPU fallback must always be gated behind explicit user consent, and only
    # ever used after the NPU has been tried — never as a silent default.
    gpu_usage_pattern = r"\bCuda\b|DirectML.*Gpu|GpuDevice|\bUseGpu\b|\.Gpu\(|GpuFallback|ExecutionProvider\.Gpu"
    consent_gate_pattern = r"IGpuFallbackConsentService|GpuFallbackConsentService|RequestGpuFallbackConsent|InferenceHardwareSelector"
    silent_gpu_default_pattern = r"UseGpuByDefault\s*=\s*true|DefaultToGpu\s*=\s*true|PreferGpu\s*=\s*true"

    gpu_hits = _any_contains(all_code, gpu_usage_pattern)
    has_consent_gate = bool(_any_contains(all_code, consent_gate_pattern))
    silent_default_hits = _any_contains(all_code, silent_gpu_default_pattern, re.IGNORECASE)

    if silent_default_hits:
        r.findings.append(Finding(
            FAIL, "gpu-fallback-consent",
            "GPU is configured as a silent default (e.g. UseGpuByDefault/PreferGpu = true). "
            "The app must always try the NPU first and only use GPU after explicit user consent.",
            file=str(silent_default_hits[0].relative_to(root)),
            fix="Remove default-GPU flags. Gate any GPU fallback behind IGpuFallbackConsentService.",
        ))
    elif gpu_hits and not has_consent_gate:
        r.findings.append(Finding(
            FAIL, "gpu-fallback-consent",
            f"GPU inference code detected ({len(gpu_hits)} files) without a user-consent gate. "
            "GPU fallback for large models (e.g. Whisper, Phi-3.5-Vision) must always prompt the user first.",
            file=str(gpu_hits[0].relative_to(root)),
            fix="Add GpuFallbackConsentService and route GPU usage through InferenceHardwareSelector.",
            auto_fix=True
        ))
    elif gpu_hits and has_consent_gate:
        r.findings.append(Finding(PASS, "gpu-fallback-consent", "GPU fallback correctly gated behind explicit user consent."))
    else:
        r.findings.append(Finding(PASS, "gpu-fallback-consent", "No GPU fallback code detected — NPU/mock-only pipeline (compliant)."))

    return r


# ─── Section 4: NPU Validation ────────────────────────────────────────────────

def check_npu(root: Path) -> SectionResult:
    r = SectionResult("4. NPU Validation Requirements")

    all_code = _find_files(root, ".cs", ".xaml")

    npu_detect = r"npu|NpuDevice|DirectML.*npu|DeviceCapabilities"
    if _any_contains(all_code, npu_detect, re.IGNORECASE):
        r.findings.append(Finding(PASS, "npu-detection", "NPU detection code found."))
    else:
        r.findings.append(Finding(
            FAIL, "npu-detection",
            "No NPU detection code found.",
            fix="Add NPU detection and accessibility check at startup.",
            auto_fix=True
        ))

    telemetry_pattern = r"latency|token.*speed|throughput|ModelLoad|InferenceTime|TokensPerSec"
    if _any_contains(all_code, telemetry_pattern, re.IGNORECASE):
        r.findings.append(Finding(PASS, "telemetry", "Inference telemetry (latency/throughput) code found."))
    else:
        r.findings.append(Finding(
            WARN, "telemetry",
            "No inference telemetry captured. Add latency, token speed, throughput, and model load time.",
            fix="Add NpuTelemetryService to capture and store inference metrics locally.",
            auto_fix=True
        ))

    metrics_ui = r"tokens.?per.?sec|response.?time|npu.?status|memory.?util|model.?size"
    if _any_contains(all_code, metrics_ui, re.IGNORECASE):
        r.findings.append(Finding(PASS, "metrics-display", "Metrics display (tokens/sec, response time, etc.) found in UI."))
    else:
        r.findings.append(Finding(
            WARN, "metrics-display",
            "No metrics display in UI. Demos should show tokens/sec, response time, model size, NPU status, memory.",
            fix="Add metrics panel to UI.",
            auto_fix=True
        ))

    return r


# ─── Section 5: Model Management ─────────────────────────────────────────────

def check_models(root: Path) -> SectionResult:
    r = SectionResult("5. Model Management")

    all_code = _find_files(root, ".cs", ".json")
    model_json = [f for f in all_code if re.search(r"model", f.name, re.IGNORECASE) and f.suffix == ".json"]

    if model_json:
        r.findings.append(Finding(PASS, "model-metadata", "Model metadata JSON file found."))
    else:
        r.findings.append(Finding(
            WARN, "model-metadata",
            "No model metadata file found. Track model version, source, install date, and size.",
            fix="Add models/model_catalog.json with version, source, installDate, sizeMb, npuOptimized fields.",
            auto_fix=True
        ))

    versioning = r"ModelVersion|model_version|version.*track|rollback|side.?by.?side"
    if _any_contains(all_code, versioning, re.IGNORECASE):
        r.findings.append(Finding(PASS, "model-versioning", "Model version tracking detected."))
    else:
        r.findings.append(Finding(
            WARN, "model-versioning",
            "No model version tracking or rollback support found.",
            fix="Add model version tracking to ModelManagementService."
        ))

    npu_model = r"npu.?optim|quantiz|INT4|INT8|onnx"
    if _any_contains(all_code, npu_model, re.IGNORECASE):
        r.findings.append(Finding(PASS, "npu-models", "NPU-optimized model references (ONNX/quantization) found."))
    else:
        r.findings.append(Finding(
            WARN, "npu-models",
            "No NPU-optimized model format (ONNX/quantized) references found.",
            fix="Prioritize ONNX/INT4-quantized models optimized for NPU."
        ))

    return r


# ─── Section 6: RAG ──────────────────────────────────────────────────────────

def check_rag(root: Path) -> SectionResult:
    r = SectionResult("6. Retrieval-Augmented Generation (RAG)")

    all_code = _find_files(root, ".cs", ".py", ".json", ".csproj")

    cloud_vectors = r"pinecone|weaviate|qdrant\.cloud|azure.*cognitive.*search|openai.*embed"
    if _any_contains(all_code, cloud_vectors, re.IGNORECASE):
        r.findings.append(Finding(
            FAIL, "no-cloud-vectors",
            "Cloud vector service detected. All vectors/embeddings must be stored locally.",
            fix="Replace cloud vector store with local SQLite vector store or embedded database."
        ))
    else:
        r.findings.append(Finding(PASS, "no-cloud-vectors", "No cloud vector services detected."))

    local_vector = r"sqlite.*vector|vector.*sqlite|Microsoft\.ML|LiteDB|local.*embed|embed.*local"
    rag_files = _any_contains(all_code, local_vector, re.IGNORECASE)
    has_rag = bool(_any_contains(all_code, r"\bembeddings?\b|vector.*store|\brag\b|\bretriev", re.IGNORECASE))

    if has_rag and not rag_files:
        r.findings.append(Finding(
            WARN, "local-vectors",
            "RAG/embedding code detected but no local vector store found.",
            fix="Use SQLite-vec or Microsoft.ML for local embedding storage."
        ))
    elif rag_files:
        r.findings.append(Finding(PASS, "local-vectors", "Local vector store implementation detected."))

    return r


# ─── Section 7: Security & Privacy ───────────────────────────────────────────

def check_security(root: Path) -> SectionResult:
    r = SectionResult("7. Security & Privacy")

    all_files = _find_files(root, ".cs", ".md", ".txt", ".xaml", ".json")
    readme_files = list(root.glob("README*")) + list(root.glob("readme*"))
    disclaimer_files = list(root.glob("DISCLAIMER*")) + list(root.glob("disclaimer*"))
    license_files = list(root.glob("LICENSE*")) + list(root.glob("license*"))

    # Zero data egress check
    egress_patterns = r'HttpClient.*POST.*prompt|SendAsync.*prompt|fetch.*prompt.*body'
    if _any_contains(all_files, egress_patterns, re.IGNORECASE):
        r.findings.append(Finding(
            FAIL, "zero-egress",
            "Potential prompt/data egress to external endpoint detected.",
            fix="Ensure prompts, documents, metadata, and embeddings never leave the device."
        ))
    else:
        r.findings.append(Finding(PASS, "zero-egress", "No obvious prompt egress to external endpoints detected."))

    # Disclaimer
    disclaimer_pattern = r"educational|demo purpose|no production|prototype|fictitious|responsible ai|no.*sla"
    if disclaimer_files:
        r.findings.append(Finding(PASS, "disclaimer", "DISCLAIMER file present."))
    elif readme_files and re.search(disclaimer_pattern, _read(readme_files[0]), re.IGNORECASE):
        r.findings.append(Finding(PASS, "disclaimer", "Disclaimer language found in README."))
    else:
        r.findings.append(Finding(
            FAIL, "disclaimer",
            "No disclaimer found. Required language: educational/demo purposes, no production support, responsible AI, fictitious data.",
            fix="Add DISCLAIMER.md and reference it from README.",
            auto_fix=True
        ))

    # Privacy statement
    privacy_pattern = r"ai inference.*local|inference.*performed.*locally|privacy.*local|local.*privacy"
    if _any_contains(all_files, privacy_pattern, re.IGNORECASE):
        r.findings.append(Finding(PASS, "privacy-statement", "Local AI privacy statement found."))
    else:
        r.findings.append(Finding(
            WARN, "privacy-statement",
            "No local AI privacy statement found.",
            fix="Add statement: 'All AI inference is performed locally on-device. No data leaves your device.'",
            auto_fix=True
        ))

    # License
    if license_files:
        r.findings.append(Finding(PASS, "license", "LICENSE file present."))
    else:
        r.findings.append(Finding(
            WARN, "license",
            "No LICENSE file found.",
            fix="Add MIT license or appropriate open-source license.",
            auto_fix=True
        ))

    return r


# ─── Section 8: Copilot+ PC Integration ──────────────────────────────────────

def check_copilot_pc(root: Path) -> SectionResult:
    r = SectionResult("8. Copilot+ PC Integration")

    all_code = _find_files(root, ".cs", ".xaml")

    copilot_apis = r"Recall|ClickToDo|WindowsStudioEffects|LiveCaptions|OcrEngine|ImageBuffer|AIFeatureMap"
    surface_diff = r"NpuAccelerat|BatteryEfficiency|offline.*ai|on.device.*privacy"

    if _any_contains(all_code, copilot_apis, re.IGNORECASE):
        r.findings.append(Finding(PASS, "copilot-apis", "Copilot+ PC API usage detected (Recall, Studio Effects, etc.)"))
    else:
        r.findings.append(Finding(
            WARN, "copilot-apis",
            "No Copilot+ PC APIs detected (Recall, Click To Do, Studio Effects, Live Captions, OCR, Image APIs).",
            fix="Consider integrating relevant Copilot+ PC APIs to showcase Surface differentiators."
        ))

    readme_files = list(root.glob("README*"))
    if readme_files and re.search(r"npu|copilot\+|surface|offline.*ai", _read(readme_files[0]), re.IGNORECASE):
        r.findings.append(Finding(PASS, "surface-differentiators", "README mentions Surface/NPU/Copilot+ differentiators."))
    else:
        r.findings.append(Finding(
            WARN, "surface-differentiators",
            "README doesn't highlight Surface differentiators (NPU, offline AI, battery efficiency, privacy).",
            fix="Add Surface differentiators section to README.",
            auto_fix=True
        ))

    return r


# ─── Section 9: Resilience & Reliability ──────────────────────────────────────

def check_resilience(root: Path) -> SectionResult:
    r = SectionResult("9. Resilience & Reliability")

    all_code = _find_files(root, ".cs", ".xaml")

    demo_mode = r"DemoMode|MockInference|IsMockMode|demo.*mode|mock.*mode"
    if _any_contains(all_code, demo_mode, re.IGNORECASE):
        r.findings.append(Finding(PASS, "demo-mode", "Demo Mode / Mock Inference detected."))
    else:
        r.findings.append(Finding(
            FAIL, "demo-mode",
            "No Demo Mode or Mock Inference found. Required to prevent demo failures when NPU/model unavailable.",
            fix="Add DemoModeService with mock inference fallback.",
            auto_fix=True
        ))

    error_handling = r"catch.*model|ModelLoadException|try.*load.*model|graceful|retry|RecoveryGuidance"
    if _any_contains(all_code, error_handling, re.IGNORECASE):
        r.findings.append(Finding(PASS, "error-handling", "Model-load error handling detected."))
    else:
        r.findings.append(Finding(
            WARN, "error-handling",
            "No graceful model-load failure handling found.",
            fix="Add try/catch around model loading with user-friendly error messages and retry logic.",
            auto_fix=True
        ))

    return r


# ─── Section 10: Deployment Standards ────────────────────────────────────────

def check_deployment(root: Path) -> SectionResult:
    r = SectionResult("10. Deployment Standards")

    # MSIX
    msix_files = list(root.rglob("*.wapproj")) + list(root.rglob("Package.appxmanifest")) + list(root.rglob("*.msix"))
    if msix_files:
        r.findings.append(Finding(PASS, "msix", "MSIX packaging configuration found."))
    else:
        r.findings.append(Finding(
            WARN, "msix",
            "No MSIX packaging found (.wapproj / Package.appxmanifest).",
            fix="Add Windows Application Packaging Project (.wapproj) for MSIX output.",
            auto_fix=True
        ))

    # Required README sections
    readme_files = list(root.glob("README*")) + list(root.glob("readme*"))
    required_sections = {
        "setup": r"setup|install|getting.started",
        "architecture": r"architecture|design|overview",
        "model-requirements": r"model.*require|require.*model",
        "demo-script": r"demo.*script|script.*demo|how to demo",
        "troubleshooting": r"troubleshoot|known.issue|faq",
        "disclaimer": r"disclaimer|prototype|demo purpose",
    }
    if readme_files:
        readme_text = _read(readme_files[0])
        for key, pattern in required_sections.items():
            if re.search(pattern, readme_text, re.IGNORECASE):
                r.findings.append(Finding(PASS, f"readme-{key}", f"README has '{key}' section."))
            else:
                r.findings.append(Finding(
                    WARN, f"readme-{key}",
                    f"README missing '{key}' section.",
                    fix=f"Add {key} section to README.",
                    auto_fix=True
                ))

        has_local_ai_context = re.search(
            r"local\s+ai|on-?device|foundry\s+local|onnx|npu",
            readme_text,
            re.IGNORECASE,
        )
        has_architecture_flow = re.search(
            r"```mermaid|flowchart|graph\s+(TD|LR)|architecture\s+flow|local\s+ai\s+.*flow",
            readme_text,
            re.IGNORECASE,
        )
        if has_local_ai_context and has_architecture_flow:
            r.findings.append(Finding(
                PASS,
                "readme-local-ai-architecture-flow",
                "README includes an architecture diagram or flow showing how local AI is used.",
            ))
        else:
            r.findings.append(Finding(
                WARN,
                "readme-local-ai-architecture-flow",
                "README is missing an architecture diagram or flow that shows how local AI is used.",
                fix="Add a Mermaid or ASCII architecture flow covering the app, "
                    "local AI orchestration, NPU-first inference, approved GPU "
                    "fallback, and on-device model storage.",
                auto_fix=True
            ))
    else:
        r.findings.append(Finding(
            FAIL, "readme",
            "No README file found.",
            fix="Create README.md with all required sections.",
            auto_fix=True
        ))

    return r


# ─── Master runner ────────────────────────────────────────────────────────────

CHECKS: list[Callable[[Path], SectionResult]] = [
    check_branding,
    check_architecture,
    check_inference,
    check_npu,
    check_models,
    check_rag,
    check_security,
    check_copilot_pc,
    check_resilience,
    check_deployment,
]


def run_all_checks(repo_root: Path) -> list[SectionResult]:
    return [check(repo_root) for check in CHECKS]
