"""
Zava Live Build -- the on-stage flow for `python harness.py --idea "..."`.

  [1] Understand the idea          (capabilities, cues)
  [2] Select the models            (live Foundry Local catalog, NPU-only, pinned)
  [3] Generate the app             (real WinUI 3 + Foundry Local SDK app)
  [4] Guardrails and compliance    (Surface AI Factory sections 1-10 + 11 Live Event Readiness)
  [5] Preflight                    (build, NPU warm-up, replay recording, Go/No-Go)
  [6] Publish                      (local commit, optional push to the GitHub org)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import stage
from ai_assist import tailor_app_spec
from best_practices import run_all_checks
from event_builder import create_event_app, slug
from event_checks import check_event_readiness
from foundry_catalog import load_catalog
from guardrails import GuardrailRegistry, capability_gap_options, model_not_cached_options
from model_selector import analyse_idea, best_cached_alternative, default_app_spec, select_models
from remediations import apply_guardrail_decisions, run_all_remediations

TOTAL = 6

LOCAL_APPS_DIR = Path.home() / "ZavaLiveBuilds"


def local_work_dir(requested: str | None) -> Path:
    """Generated apps live on this device, outside OneDrive. Cloud-synced folders slow builds
    (bin/obj churn) and can evict model or build files, so they are never used."""
    if not requested:
        return LOCAL_APPS_DIR
    path = Path(requested).expanduser().resolve()
    if is_cloud_synced(path):
        stage.warn(f"{path} is inside a cloud-synced folder. Building in {LOCAL_APPS_DIR} instead.")
        return LOCAL_APPS_DIR
    return path


def is_cloud_synced(path: Path) -> bool:
    roots = [os.environ.get(k) for k in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")]
    text = str(path).lower()
    return "onedrive" in text or any(r and text.startswith(r.lower()) for r in roots)


class Budget:
    """The whole --idea run must finish inside --budget-minutes (default 25)."""

    def __init__(self, minutes: int):
        self.total = max(5, minutes) * 60
        self.started = time.monotonic()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def remaining(self) -> float:
        return self.total - self.elapsed

    def clock(self) -> str:
        return f"{self.elapsed / 60:.1f} of {self.total / 60:.0f} min used"


def run_event_build(args) -> int:
    idea = args.idea.strip()
    budget = Budget(getattr(args, "budget_minutes", 25))
    stage.banner("Idea in. A real local AI app out.")
    stage.info(f'Idea: "{idea}"')
    stage.info(f"Time budget: {budget.total / 60:.0f} minutes. The app is built on this device in "
               f"{local_work_dir(args.work_dir)}.")

    # ── 1. Understand the idea ─────────────────────────────────────────────
    stage.stage(1, TOTAL, "Understanding the idea")
    analysis = analyse_idea(idea)
    rows = [("Primary workload", analysis.primary)]
    rows += [(cap, ", ".join(words)) for cap, words in analysis.cues.items()]
    stage.card("WHAT THE APP NEEDS", rows or [("Primary workload", "general assistant")])

    # ── 2. Select models ───────────────────────────────────────────────────
    stage.stage(2, TOTAL, "Selecting on-device models")
    catalog = load_catalog(allow_live=not args.offline_catalog)
    for note in catalog.notes:
        stage.warn(note)
    stage.info(f"Read {len(catalog.variants)} model builds from the {catalog.source} Foundry Local catalog; "
               f"{len(catalog.npu('chat-completion'))} run on the NPU.")
    plan = select_models(analysis, catalog, pin_alias=args.model)
    if plan.primary is None:
        stage.fail("No NPU chat model is available on this device. A bulletproof NPU demo is not possible here.")
        return 2

    event_registry = GuardrailRegistry(interactive=not args.non_interactive)
    # A presenter-pinned model is never silently swapped for a cached one.
    alternative = None if args.model else best_cached_alternative(
        catalog, exclude_alias=plan.primary.alias, analysis=analysis)
    if not plan.primary.cached:
        event_registry.event(
            guardrail="model-cached",
            requirement="The stage model must be on the device before the session, so nothing downloads live.",
            reason=f"{plan.primary.model_id} is the best fit for this idea but is not cached on this device.",
            options=model_not_cached_options(plan.primary.model_id, plan.primary.size_mb / 1024,
                                              alternative.alias if alternative else None),
        )
    for gap in plan.gaps:
        event_registry.event(
            guardrail=f"npu-{gap.capability}",
            requirement="Every model in a stage demo runs on the NPU; other silicon needs explicit consent.",
            reason=gap.detail,
            options=capability_gap_options(gap.capability, gap.best_available),
        )

    if event_registry.escalations:
        event_registry.resolve_all()
        if event_registry.aborted:
            stage.fail("Run aborted at presenter request. Nothing was built.")
            return 1
        for esc in event_registry.escalations:
            if esc.guardrail == "model-cached" and esc.resolution == "cached" and alternative:
                best = plan.primary
                plan = select_models(analysis, catalog, pin_alias=alternative.alias)
                plan.primary.reasons.insert(
                    0, f"Already on this device. Best fit {best.alias} would need a "
                       f"{best.size_mb / 1024:.1f} GB download, so it was not used for the show.")
                plan.considered = [f"{best.alias}: best fit, not cached ({best.size_mb / 1024:.1f} GB download)"]

    stage.model_card(plan)
    for line in plan.considered:
        stage.info(f"  considered: {line}")

    spec = default_app_spec(idea, analysis, name=args.name)
    assist_model = _assist_model(catalog)
    # The assist only improves the title and samples, and the industry spec already covers
    # both. It is skipped when the idea already contains example requests.
    has_examples = len(spec.sample_prompts) >= 3 and any(q in idea for q in spec.sample_prompts[:1])
    if not args.no_ai_assist and catalog.endpoint and assist_model and not has_examples:
        stage.info(f"Asking {assist_model.alias} (running locally) to suggest sample requests "
                   f"(up to {args.assist_timeout}s):")
        print("  ", end="", flush=True)
        tailored, message = tailor_app_spec(
            idea, analysis, catalog.endpoint, assist_model.id, timeout=args.assist_timeout,
            echo=lambda t: print(stage.c(t.replace("\n", "\n  "), stage.DIM), end="", flush=True))
        print()
        if tailored:
            if not args.name and tailored.title:
                spec.title = tailored.title
            spec.tagline = tailored.tagline or spec.tagline
            spec.sample_prompts = tailored.sample_prompts
            spec.modes[0]["samplePrompts"] = tailored.sample_prompts
            spec.generated_by = tailored.generated_by
            stage.ok(f"Samples suggested by {message}")
        else:
            stage.warn(f"Local AI assist skipped: {message}. Using the {spec.industry_name} profile.")
    elif has_examples:
        stage.ok("Using the example requests written into the idea as the demo samples.")
    stage.card("APP DESIGN", [
        ("Title", spec.title),
        ("What it does", spec.tagline),
        ("Industry", f"{spec.industry_name} (theme, wording and samples)"),
        ("Modes", ", ".join(m["name"] for m in spec.modes)),
        ("Grounded in", "local knowledge base" if spec.knowledge else "the request only"),
        ("Why local", "; ".join(d["title"] for d in spec.local_drivers)),
    ], footer=[f'{m["name"]}: {m["samplePrompts"][0]}' for m in spec.modes])

    repo_name = slug(args.name or spec.title)
    work_dir = local_work_dir(args.work_dir)

    if args.plan_only:
        out = work_dir / f"{repo_name}.plan.json"
        work_dir.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"plan": plan.to_dict(), "spec": spec.to_dict()}, indent=2), encoding="utf-8")
        stage.ok(f"Plan only. Saved to {out}")
        return 0

    # ── 3. Generate the app ────────────────────────────────────────────────
    stage.stage(3, TOTAL, "Generating the app")
    root = create_event_app(spec, plan, repo_name, work_dir, overwrite=args.overwrite)
    stage.ok(f"{spec.title} created on this device at {root}")
    stage.info(f"{spec.industry_name} design, {len(spec.modes)} mode(s)"
               + (", local knowledge base" if spec.knowledge else "")
               + ", 'why local' story, details ribbon, presenter shortcuts, labelled replay.")

    # ── 4. Guardrails and compliance ───────────────────────────────────────
    stage.stage(4, TOTAL, "Applying guardrails and checking compliance")
    from harness import print_report, save_report  # local import avoids a cycle

    registry = GuardrailRegistry(interactive=not args.non_interactive)
    stage.info("[P0] Branding and attribution first (Zava + Surface AI Factory watermark)")
    applied = run_all_remediations(root, repo_name, registry)
    if registry.escalations:
        registry.resolve_all()
        if registry.aborted:
            stage.fail("Run aborted at presenter request. Nothing was pushed.")
            return 1
        for category, actions in apply_guardrail_decisions(root, registry).items():
            applied.setdefault(category, []).extend(actions)

    def report() -> list:
        sections = run_all_checks(root) + [check_event_readiness(root)]
        path = root / ".surface-ai-factory" / "compliance-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        save_report(sections, repo_name, applied, path, registry.summary() + event_registry.summary())
        return sections

    sections = report()
    print_report(sections, repo_name, applied)

    # ── 5. Preflight ───────────────────────────────────────────────────────
    go = None
    if args.skip_preflight:
        stage.stage(5, TOTAL, "Preflight skipped (--skip-preflight)")
        stage.warn(f"Run before the session: python harness.py --preflight \"{root}\"")
    else:
        stage.stage(5, TOTAL, f"Preflight: build, warm-up, rehearsal ({budget.clock()})")
        from preflight import run_preflight
        # Keep 60s back for the report and commit; the warm-up gets what is left.
        warm_budget = int(min(args.warmup_timeout, max(120, budget.remaining - 60)))
        go = run_preflight(root, download=True, warmup_timeout=warm_budget,
                           full_rehearsal=getattr(args, "full_rehearsal", False), budget=budget)
        sections = report()
        event_section = sections[-1]
        print_report([event_section], repo_name)

    # ── 6. Publish ─────────────────────────────────────────────────────────
    stage.stage(6, TOTAL, "Publishing")
    _commit_locally(root)
    if args.no_push:
        stage.info("--no-push set: kept local.")
    elif not args.github_token:
        stage.info("No GitHub token set: kept local. Set GITHUB_TOKEN to publish.")
    else:
        from github_ops import ensure_target_repo, push_to_target
        ensure_target_repo(args.github_token, args.target_org, repo_name, f"Zava Live Build: {spec.title}")
        url = push_to_target(root, args.github_token, args.target_org, repo_name, branch="main",
                             commit_message="feat: Zava Live Build event app [harness-agent]")
        if url:
            stage.ok(f"Published: {url}")

    registry.print_outcome()
    event_registry.print_outcome()
    stage.card("READY", [
        ("App", spec.title),
        ("Folder", str(root)),
        ("Launch", f'powershell -File "{root / "tools" / "Start-Demo.ps1"}"'),
        ("Runbook", str(root / "docs" / "DEMO-RUNBOOK.md")),
        ("Build time", f"{budget.elapsed / 60:.1f} min (budget {budget.total / 60:.0f})"),
        ("Status", "GO FOR SHOW" if go else ("run preflight" if go is None else "NO-GO, see above")),
    ], colour=stage.GREEN if go else stage.YELLOW)
    if go and args.launch:
        subprocess.Popen(["powershell", "-NoProfile", "-File", str(root / "tools" / "Start-Demo.ps1")])
    return 0 if go is not False else 3


ASSIST_PREFERENCES = ("qwen2.5-1.5b", "qwen2.5-coder-1.5b", "phi-4-mini")


def _assist_model(catalog):
    """A cached NPU model big enough to return valid JSON, small enough to answer fast."""
    for alias in ASSIST_PREFERENCES:
        cached = [v for v in catalog.by_alias(alias) if v.device == "NPU" and v.cached]
        if cached:
            return cached[0]
    return None


def _commit_locally(root: Path) -> None:
    def git(*a):
        return subprocess.run(["git", *a], cwd=str(root), capture_output=True, text=True)

    if not (root / ".git").exists():
        git("init", "-b", "main")
    git("add", "-A")
    if not git("status", "--porcelain").stdout.strip():
        stage.info("Nothing new to commit.")
        return
    result = git("-c", "user.name=Surface AI Factory Harness", "-c", "user.email=surface-ai-factory@microsoft.com",
                 "commit", "-m", "feat: Zava Live Build event app")
    if result.returncode == 0:
        stage.ok("Committed locally on branch main.")
    else:
        stage.warn(f"Local commit failed: {result.stderr.strip()[:120]}")


def run_preflight_only(args) -> int:
    stage.banner("Preflight: is this device ready for the show?")
    from preflight import run_preflight
    app_dir = Path(args.preflight)
    if not app_dir.exists() and (LOCAL_APPS_DIR / args.preflight).exists():
        app_dir = LOCAL_APPS_DIR / args.preflight   # allow just the app folder name
    go = run_preflight(app_dir, download=not args.no_download,
                       warmup_timeout=args.warmup_timeout,
                       full_rehearsal=getattr(args, "full_rehearsal", False))
    return 0 if go else 3


if __name__ == "__main__":
    sys.exit("Run via harness.py --idea / --preflight")
