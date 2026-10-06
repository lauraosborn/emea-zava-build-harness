"""
Zava Live Build -- preflight (Go / No-Go).

Run before every session. Proves the demo will work on THIS device:

  * NPU present and healthy
  * Foundry Local installed, service running, an NPU execution provider registered
  * pinned models cached (downloads them if not, while you are still online)
  * .NET SDK present and the app builds
  * the app's own --warmup run loads the model on the NPU, compiles it into the
    app's OpenVINO cache, runs every sample prompt live and records the labelled
    replay safety net
  * power: on mains

Writes .surface-ai-factory/preflight-report.json into the app folder.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import foundry_catalog
import stage

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def _ps(command: str, timeout: int = 60) -> str:
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
        return proc.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def check_npu() -> tuple[str, str, str]:
    out = _ps("Get-PnpDevice -Class ComputeAccelerator -ErrorAction SilentlyContinue | "
              "ForEach-Object { \"$($_.FriendlyName)|$($_.Status)\" }")
    devices = [line.split("|") for line in out.splitlines() if "|" in line]
    healthy = [name for name, status in devices if status.strip().upper() == "OK"]
    if healthy:
        return ("NPU detected", PASS, healthy[0])
    if devices:
        return ("NPU detected", FAIL, f"{devices[0][0]} status {devices[0][1]}")
    return ("NPU detected", FAIL, "no ComputeAccelerator device; this is not a Copilot+ PC")


def check_power() -> tuple[str, str, str]:
    out = _ps("$b = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue; "
              "if ($b) { \"$($b.BatteryStatus)|$($b.EstimatedChargeRemaining)\" }")
    if not out:
        return ("Power", PASS, "no battery (desktop)")
    status, charge = (out.split("|") + ["", ""])[:2]
    on_ac = status.strip() in ("2", "3", "6", "7", "8", "9", "11")
    return ("Power", PASS if on_ac else WARN,
            f"on mains, {charge}%" if on_ac else f"ON BATTERY at {charge}%: plug in for the show")


def check_foundry() -> tuple[list[tuple[str, str, str]], str | None, list[str]]:
    results = []
    if not foundry_catalog.foundry_cli():
        return [("Foundry Local installed", FAIL, "winget install Microsoft.FoundryLocal")], None, []
    results.append(("Foundry Local installed", PASS, foundry_catalog._run_cli("--version").strip()[:40]))
    url, eps = foundry_catalog.service_status()
    if not url:
        url, eps = foundry_catalog.start_service()
    results.append(("Foundry Local service", PASS if url else FAIL, url or "could not start the service"))
    npu_eps = [e for e in eps if e in foundry_catalog.NPU_EXECUTION_PROVIDERS]
    results.append(("NPU execution provider", PASS if npu_eps else WARN,
                    ", ".join(npu_eps) if npu_eps else "none registered yet (the app registers it on first run)"))
    return results, url, eps


def ensure_models_cached(config: dict, base_url: str | None, download: bool,
                         download_timeout: int = 3600) -> list[tuple[str, str, str]]:
    results = []
    cached: set[str] = set()
    if base_url:
        try:
            import requests
            cached = set(requests.get(f"{base_url}/openai/models", timeout=20).json())
        except Exception:  # noqa: BLE001
            cached = set()
    if not cached:
        cached = foundry_catalog.parse_cache_list(foundry_catalog._run_cli("cache", "list"))

    for role in ("primary", "backup"):
        ref = config.get(role)
        if not ref:
            continue
        model_id = ref["modelId"]
        label = f"{role.capitalize()} model cached"
        if model_id in cached:
            results.append((label, PASS, model_id))
            continue
        if not download:
            results.append((label, FAIL if role == "primary" else WARN, f"{model_id} not cached"))
            continue
        stage.info(f"Downloading {model_id} (up to {download_timeout // 60} min)...")
        out = foundry_catalog._run_cli("model", "download", model_id, timeout=download_timeout)
        ok = "error" not in out.lower() or model_id in foundry_catalog._run_cli("cache", "list")
        results.append((label, PASS if ok else FAIL, f"{model_id} {'downloaded' if ok else 'download failed'}"))
    return results


def check_dotnet() -> tuple[str, str, str]:
    exe = shutil.which("dotnet")
    if not exe:
        return (".NET SDK", FAIL, "install the .NET 9 SDK")
    out = subprocess.run([exe, "--list-sdks"], capture_output=True, text=True).stdout
    has9 = any(line.startswith(("9.", "10.")) for line in out.splitlines())
    return (".NET SDK", PASS if has9 else FAIL, "9.x present" if has9 else "needs .NET 9 SDK")


def platform_name() -> str:
    machine = platform.machine().lower()
    arch = os.environ.get("PROCESSOR_ARCHITEW6432", os.environ.get("PROCESSOR_ARCHITECTURE", machine)).lower()
    return "ARM64" if "arm" in arch else "x64"


def build_app(app_root: Path, project: Path) -> tuple[tuple[str, str, str], Path | None]:
    """Local, unpackaged build for this device only: no MSIX, no publish, no trimming.
    Restore is skipped when it already ran (rebuilds take seconds)."""
    plat = platform_name()
    started = time.perf_counter()
    restored = (project.parent / "obj" / "project.assets.json").exists()
    cmd = ["dotnet", "build", str(project), "-c", "Release", f"-p:Platform={plat}", "--nologo",
           "-v", "minimal", "-m", "-p:GenerateAppxPackageOnBuild=false", "-p:AppxPackageSigningEnabled=false",
           "-p:DebugType=none", "-p:GenerateDocumentationFile=false"]
    if restored:
        cmd.append("--no-restore")
    proc = subprocess.run(cmd, cwd=str(app_root), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 and restored:
        cmd.remove("--no-restore")  # package set changed since the last restore
        proc = subprocess.run(cmd, cwd=str(app_root), capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        log = app_root / ".surface-ai-factory" / "build.log"
        log.parent.mkdir(exist_ok=True)
        log.write_text(proc.stdout + proc.stderr, encoding="utf-8")
        errors = [line for line in proc.stdout.splitlines() if " error " in line][:1]
        return ("App builds (Release)", FAIL, (errors[0][-90:] if errors else f"see {log}")), None
    exes = sorted((project.parent / "bin").rglob(f"{project.stem}.exe"), key=lambda p: p.stat().st_mtime)
    return ("App builds (Release)", PASS, f"{plat} in {elapsed:.0f}s"), (exes[-1] if exes else None)


def _app_log(exe: Path) -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / exe.stem / "logs" / f"app_{datetime.now():%Y%m%d}.log"


def _npu_memory_note(exe: Path, log_offset: int = 0) -> str:
    """Plain-language reason when the NPU ran out of memory during this warm-up (log lines after log_offset)."""
    try:
        with open(_app_log(exe), "rb") as f:
            f.seek(log_offset)
            fresh = f.read().decode("utf-8", errors="replace")
        if "OUT_OF_HOST_MEMORY" in fresh:
            others = other_npu_apps(str(exe))
            return ("The NPU ran out of memory" + (f": close {', '.join(others)} and re-run preflight" if others
                                                   else ": close other AI apps and re-run preflight"))
    except OSError:
        pass
    return ""


def run_warmup(exe: Path, app_name: str, timeout: int, full: bool = False) -> tuple[list[tuple[str, str, str]], dict]:
    report_path = Path(os.environ["LOCALAPPDATA"]) / app_name / "warmup.json"
    if report_path.exists():
        report_path.unlink()
    log_offset = _app_log(exe).stat().st_size if _app_log(exe).exists() else 0
    stage.info("Warming up inside the app: loading the model, then rehearsing one request per mode")
    stage.info(f"and as many more as fit in {timeout // 60} min. The first model compile on a device takes")
    stage.info("1 to 3 minutes and is shared by every app built afterwards.")
    started = time.perf_counter()
    args = [str(exe), "--warmup", "--deadline", str(max(60, timeout - 30))]
    if full:
        args.append("--full")
    try:
        subprocess.run(args, timeout=timeout + 60, check=False)
    except subprocess.TimeoutExpired:
        return [("Warm-up in app", FAIL, f"timed out after {timeout}s")], {}
    elapsed = time.perf_counter() - started
    if not report_path.exists():
        return [("Warm-up in app", FAIL, "no warmup.json written; check the app logs")], {}

    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    primary = report.get("primary") or {}
    samples = report.get("samples") or []
    live = [s for s in samples if s.get("live")]
    modes = report.get("modes") or []
    rehearsed_modes = {s.get("modeId") for s in live}
    all_modes = not modes or all(m in rehearsed_modes for m in modes)
    memory = _npu_memory_note(exe, log_offset)
    primary_ok = primary.get("live") and primary.get("device") == "NPU" and not primary.get("usingBackup")
    results = [
        ("Model live on this device", PASS if primary_ok else FAIL,
         f"{primary.get('modelId')} on {primary.get('device')}, ready in {primary.get('loadSeconds')}s"
         + ("" if primary_ok or not memory else f". {memory}")),
        ("Every mode rehearsed live",
         PASS if live and all_modes and len(live) == len(samples) else (WARN if live else FAIL),
         f"{len(live)} request(s) across {len(rehearsed_modes)}/{len(modes) or 1} mode(s)" + (
             f", avg {sum(s['tokensPerSecond'] for s in live) / len(live):.1f} tok/s, "
             f"first token {sum(s['firstTokenMs'] for s in live) / len(live) / 1000:.1f}s" if live else "")),
        ("Replay safety net recorded", PASS if report.get("replayRecorded") else FAIL,
         f"{report.get('replayRecorded', 0)} answers recorded on this device"),
    ]
    peak = max((s.get("peakNpuPercent") or 0 for s in live), default=0)
    results.append(("NPU activity observed", PASS if peak > 0 else WARN,
                    f"peak {peak:.0f}% during samples" if peak > 0 else
                    "counters read 0%: show Task Manager NPU graph instead"))
    backup = report.get("backup")
    if backup:
        results.append(("Backup model ready", PASS if backup.get("live") and backup.get("device") == "NPU" else WARN,
                        f"{backup.get('modelId')} ready in {backup.get('loadSeconds')}s"
                        + (" (already compiled on this device)" if backup.get("skipped") else "")))
    if report.get("error"):
        results.append(("Warm-up errors", FAIL, report["error"][:80]))
    results.append(("Warm-up duration", PASS, f"{elapsed:.0f}s"))
    return results, report


def free_npu(base_url: str | None) -> list[str]:
    """Unload models the Foundry Local service holds in memory. The NPU has limited host memory,
    and a model left loaded by another tool makes the app's model fail with
    ZE_RESULT_ERROR_OUT_OF_HOST_MEMORY. Unloading only frees memory; nothing is deleted."""
    def loaded_models() -> list[str]:
        if base_url:
            try:
                import requests
                data = requests.get(f"{base_url}/openai/loadedmodels", timeout=10).json()
                return [m if isinstance(m, str) else (m.get("id") or m.get("name") or "") for m in data or []]
            except Exception:  # noqa: BLE001
                pass
        out = foundry_catalog._run_cli("service", "ps", timeout=30)
        return re.findall(r"\s(\S+-npu:\d+|\S+-gpu:\d+|\S+-cpu:\d+)\s*$", out, flags=re.MULTILINE)

    freed = []
    for model in [m for m in loaded_models() if m]:
        # The CLI waits for an in-flight load to finish before unloading, which can take minutes.
        foundry_catalog._run_cli("model", "unload", model, timeout=240)
        freed.append(model)
    still = [m for m in loaded_models() if m]
    return [m for m in freed if m not in still]


NPU_MODULES = ("openvino_intel_npu_plugin", "qnnhtp", "amdxdna", "vaip")
IGNORED_NPU_USERS = {"inference.service.agent", "python", "pythonw"}


def other_npu_apps(own_exe: str | None = None) -> list[str]:
    """Other running apps that have an NPU runtime loaded. They share the NPU's memory."""
    pattern = "|".join(NPU_MODULES)
    out = _ps("Get-Process | ForEach-Object { $p=$_; try { if ($p.Modules | Where-Object { $_.ModuleName -match '"
              + pattern + "' }) { \"$($p.ProcessName)|$($p.Id)\" } } catch {} }", timeout=120)
    apps = []
    own = Path(own_exe).stem.lower() if own_exe else ""
    for line in out.splitlines():
        name = line.split("|")[0].strip()
        if name and name.lower() not in IGNORED_NPU_USERS and name.lower() != own and name not in apps:
            apps.append(name)
    return apps


def run_preflight(app_root: Path, download: bool = True, build: bool = True, warmup: bool = True,
                  warmup_timeout: int = 780, full_rehearsal: bool = False, budget=None) -> bool:
    app_root = app_root.resolve()
    projects = [p for p in app_root.glob("src/*/*.csproj")]
    if not projects:
        stage.fail(f"No src/<App>/<App>.csproj under {app_root}. Is this a Zava Live Build app?")
        return False
    project = projects[0]
    config_path = project.parent / "Assets" / "app-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.exists() else {}

    results: list[tuple[str, str, str]] = [check_npu(), check_power()]
    foundry_results, url, _ = check_foundry()
    results += foundry_results
    for r in results:
        _echo(r)

    # Leave at least 8 minutes of the budget for build and warm-up after any download.
    download_timeout = int(max(60, budget.remaining - 480)) if budget else 3600
    model_results = ensure_models_cached(config, url, download, download_timeout) if url else []
    for r in model_results:
        _echo(r)
    results += model_results

    dotnet = check_dotnet()
    _echo(dotnet)
    results.append(dotnet)

    warm_report: dict = {}
    if build and dotnet[1] == PASS:
        stage.info(f"Building {project.name} for this device (unpackaged, no publish)...")
        build_result, exe = build_app(app_root, project)
        _echo(build_result)
        results.append(build_result)
        if warmup and exe and not any(r[1] == FAIL for r in results):
            freed = free_npu(url)
            if freed:
                _echo(("NPU memory freed", PASS, f"unloaded {', '.join(freed)} from the Foundry Local service"))
            sharing = other_npu_apps(str(exe))
            if sharing:
                shared = ("NPU shared with other apps", WARN,
                          f"{', '.join(sharing)} {'also use' if len(sharing) > 1 else 'also uses'} the NPU; close before the show")
                _echo(shared)
                results.append(shared)
            if budget:
                warmup_timeout = int(min(warmup_timeout, max(120, budget.remaining - 60)))
            started = time.monotonic()
            warm_results, warm_report = run_warmup(exe, project.stem, warmup_timeout, full_rehearsal)
            # NPU out of memory (another app or service grabbed it): free it and retry once if time allows.
            # The model is compiled by now, so the retry loads in seconds.
            primary_failed = any(r[0] == "Model live on this device" and r[1] == FAIL for r in warm_results)
            left = (budget.remaining - 60) if budget else (warmup_timeout - (time.monotonic() - started))
            if primary_failed and "out of memory" in " ".join(r[2] for r in warm_results).lower() and left > 180:
                for r in warm_results:
                    _echo(r)
                stage.warn("The NPU ran out of memory. Freeing it and retrying the warm-up once...")
                free_npu(url)
                warm_results, warm_report = run_warmup(exe, project.stem, int(left), full_rehearsal)
            for r in warm_results:
                _echo(r)
            results += warm_results
            _copy_replay_into_repo(project, warm_report)

    go = stage.go_board(results)
    report_path = app_root / ".surface-ai-factory" / "preflight-report.json"
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text(json.dumps({
        "generated": datetime.now(timezone.utc).isoformat(),
        "go": go,
        "checks": [{"check": n, "status": s, "detail": d} for n, s, d in results],
        "warmup": warm_report,
    }, indent=2), encoding="utf-8")
    stage.info(f"Report: {report_path}")
    return go


def _copy_replay_into_repo(project: Path, warm_report: dict) -> None:
    """Ship the recorded replay with the repo so a fresh clone still has a safety net."""
    if not warm_report.get("replayRecorded"):
        return
    src = Path(os.environ["LOCALAPPDATA"]) / project.stem / "replay.json"
    if src.exists():
        shutil.copyfile(src, project.parent / "Assets" / "replay.json")


def _echo(result: tuple[str, str, str]) -> None:
    name, status, detail = result
    {PASS: stage.ok, WARN: stage.warn, FAIL: stage.fail}[status](f"{name}: {detail}")
