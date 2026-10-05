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


def ensure_models_cached(config: dict, base_url: str | None, download: bool) -> list[tuple[str, str, str]]:
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
        stage.info(f"Downloading {model_id} (do this on venue-independent Wi-Fi)...")
        out = foundry_catalog._run_cli("model", "download", model_id, timeout=3600)
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
    plat = platform_name()
    started = time.perf_counter()
    proc = subprocess.run(
        ["dotnet", "build", str(project), "-c", "Release", f"-p:Platform={plat}", "--nologo", "-v", "minimal"],
        cwd=str(app_root), capture_output=True, text=True, encoding="utf-8", errors="replace")
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        log = app_root / ".surface-ai-factory" / "build.log"
        log.parent.mkdir(exist_ok=True)
        log.write_text(proc.stdout + proc.stderr, encoding="utf-8")
        errors = [line for line in proc.stdout.splitlines() if " error " in line][:1]
        return ("App builds (Release)", FAIL, (errors[0][-90:] if errors else f"see {log}")), None
    exes = sorted((project.parent / "bin").rglob(f"{project.stem}.exe"), key=lambda p: p.stat().st_mtime)
    return ("App builds (Release)", PASS, f"{plat} in {elapsed:.0f}s"), (exes[-1] if exes else None)


def run_warmup(exe: Path, app_name: str, timeout: int) -> tuple[list[tuple[str, str, str]], dict]:
    report_path = Path(os.environ["LOCALAPPDATA"]) / app_name / "warmup.json"
    if report_path.exists():
        report_path.unlink()
    stage.info("Warming up inside the app: loading on the NPU, compiling, running every sample prompt...")
    stage.info("First time on a device this takes 1 to 4 minutes. Later runs take seconds.")
    started = time.perf_counter()
    try:
        subprocess.run([str(exe), "--warmup"], timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return [("Warm-up in app", FAIL, f"timed out after {timeout}s")], {}
    elapsed = time.perf_counter() - started
    if not report_path.exists():
        return [("Warm-up in app", FAIL, "no warmup.json written; check the app logs")], {}

    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    primary = report.get("primary") or {}
    samples = report.get("samples") or []
    live = [s for s in samples if s.get("live")]
    results = [
        ("Primary model live on NPU",
         PASS if primary.get("live") and primary.get("device") == "NPU" and not primary.get("usingBackup") else FAIL,
         f"{primary.get('modelId')} on {primary.get('device')}, ready in {primary.get('loadSeconds')}s"),
        ("Sample prompts run live",
         PASS if samples and len(live) == len(samples) else (WARN if live else FAIL),
         f"{len(live)}/{len(samples)} live" + (
             f", avg {sum(s['tokensPerSecond'] for s in live) / len(live):.1f} tok/s, "
             f"first token {sum(s['firstTokenMs'] for s in live) / len(live) / 1000:.1f}s" if live else "")),
        ("Replay safety net recorded", PASS if report.get("replayRecorded") else FAIL,
         f"{report.get('replayRecorded', 0)} outputs recorded on this NPU"),
    ]
    peak = max((s.get("peakNpuPercent") or 0 for s in live), default=0)
    results.append(("NPU activity observed", PASS if peak > 0 else WARN,
                    f"peak {peak:.0f}% during samples" if peak > 0 else
                    "counters read 0%: show Task Manager NPU graph instead"))
    backup = report.get("backup")
    if backup:
        results.append(("Backup model warm on NPU", PASS if backup.get("live") and backup.get("device") == "NPU" else WARN,
                        f"{backup.get('modelId')} ready in {backup.get('loadSeconds')}s"))
    if report.get("error"):
        results.append(("Warm-up errors", FAIL, report["error"][:80]))
    results.append(("Warm-up duration", PASS, f"{elapsed:.0f}s"))
    return results, report


def run_preflight(app_root: Path, download: bool = True, build: bool = True, warmup: bool = True,
                  warmup_timeout: int = 900) -> bool:
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

    model_results = ensure_models_cached(config, url, download) if url else []
    for r in model_results:
        _echo(r)
    results += model_results

    dotnet = check_dotnet()
    _echo(dotnet)
    results.append(dotnet)

    warm_report: dict = {}
    if build and dotnet[1] == PASS:
        stage.info(f"Building {project.name} in Release...")
        build_result, exe = build_app(app_root, project)
        _echo(build_result)
        results.append(build_result)
        if warmup and exe and not any(r[1] == FAIL for r in results):
            warm_results, warm_report = run_warmup(exe, project.stem, warmup_timeout)
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
