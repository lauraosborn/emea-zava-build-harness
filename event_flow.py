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
import subprocess
import sys
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


def run_event_build(args) -> int:
    idea = args.idea.strip()
    stage.banner("Idea in. Bulletproof NPU app out.")
    stage.info(f'Idea: "{idea}"')

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
    if not args.no_ai_assist and catalog.endpoint and assist_model:
        stage.info(f"Asking {assist_model.alias} on the NPU to design the app (the harness runs locally too):")
        print("  ", end="", flush=True)
        tailored, message = tailor_app_spec(
            idea, analysis, catalog.endpoint, assist_model.id, timeout=args.assist_timeout,
            echo=lambda t: print(stage.c(t.replace("\n", "\n  "), stage.DIM), end="", flush=True))
        print()
        if tailored:
            title = args.name or tailored.title
            spec = default_app_spec(idea, analysis, name=title)
            spec.tagline = tailored.tagline or spec.tagline
            spec.input_label = tailored.input_label or spec.input_label
            spec.sample_prompts = tailored.sample_prompts
            spec.generated_by = tailored.generated_by
            stage.ok(f"App spec designed by {message}")
        else:
            stage.warn(f"Local AI assist skipped: {message}. Using the built-in spec.")
    stage.card("APP SPEC", [
        ("Title", spec.title),
        ("Tagline", spec.tagline),
        ("Spec by", spec.generated_by),
    ], footer=spec.sample_prompts)

    repo_name = slug(args.name or spec.title)
    work_dir = Path(args.work_dir).resolve()

    if args.plan_only:
        out = work_dir / f"{repo_name}.plan.json"
        work_dir.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"plan": plan.to_dict(), "spec": spec.to_dict()}, indent=2), encoding="utf-8")
        stage.ok(f"Plan only. Saved to {out}")
        return 0

    # ── 3. Generate the app ────────────────────────────────────────────────
    stage.stage(3, TOTAL, "Generating the app")
    root = create_event_app(spec, plan, repo_name, work_dir, overwrite=args.overwrite)
    stage.ok(f"WinUI 3 app with Foundry Local SDK created at {root}")
    stage.info("Includes: live inference panel, NPU graph, pinned models, watchdog, labelled replay,")
    stage.info("presenter shortcuts, --warmup mode, runbook, Zava branding and the Surface AI Factory watermark.")

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
        stage.stage(5, TOTAL, "Preflight: build, NPU warm-up, replay recording")
        from preflight import run_preflight
        go = run_preflight(root, download=True, warmup_timeout=args.warmup_timeout)
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
    go = run_preflight(Path(args.preflight), download=not args.no_download,
                       warmup_timeout=args.warmup_timeout)
    return 0 if go else 3


if __name__ == "__main__":
    sys.exit("Run via harness.py --idea / --preflight")
