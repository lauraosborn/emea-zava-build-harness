#!/usr/bin/env python3
"""
Surface AI Factory — Best Practices Harness Agent (Zava Live Build event edition)
=================================================================================
Takes a GitHub repo, a plain-English request for a new local AI app, or (event
mode) a local app idea. Audits against the Surface AI Factory best practices,
applies automated remediations, and pushes the result to https://github.com/AI-Runs-Local.

Usage:
    python harness.py --repo <github-url> [options]
    python harness.py --new "build a local AI app that..." --name <repo-name> [options]
    python harness.py --idea "a local AI app that..." [--name <app-name>] [options]
    python harness.py --preflight <generated-app-folder>

Options:
    --repo              GitHub repository URL to process
    --new              Build a new app/agent/solution from this request
    --name             Repository name for --new mode
    --target-org        GitHub org to push results to (default: AI-Runs-Local)
    --github-token      GitHub PAT with repo/org write access
                        (or set env var GITHUB_TOKEN)
    --work-dir          Local working directory (default: .harness-workspace)
    --report-only       Run compliance check only; do not apply fixes or push
    --no-push           Apply fixes locally but do not push to GitHub
    --ai-endpoint       OpenAI-compatible local AI endpoint for enhanced analysis
                        (default: http://localhost:5272/v1 — Foundry Local)
    --ai-model          Model ID to use for AI-assisted analysis
                        (default: phi-3.5-mini-instruct)
    --verbose           Show detailed output

Example:
    python harness.py --repo <paste-your-github-repo-url-here> \\
                      --github-token ghp_xxxx
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path

from best_practices import run_all_checks, PASS, WARN, FAIL
from remediations import run_all_remediations, apply_guardrail_decisions
from guardrails import GuardrailRegistry
from github_ops import (
    clone_repo,
    ensure_target_repo,
    push_to_target,
    repo_name_from_url,
    get_default_branch,
)
from scratch_builder import create_new_solution, slugify_repo_name

# ─── ANSI colours ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

STATUS_COLOUR = {PASS: GREEN, WARN: YELLOW, FAIL: RED}
STATUS_ICON   = {PASS: "[PASS]", WARN: "[WARN]", FAIL: "[FAIL]"}


def colour(text: str, code: str) -> str:
    if sys.stdout.isatty():
        return f"{code}{text}{RESET}"
    return text


# ─── AI-assisted analysis (optional, uses local Foundry endpoint) ─────────────

def ai_analyse_file(content: str, filename: str, endpoint: str, model: str) -> str | None:
    """
    Ask the local AI model for best-practice suggestions on a code file.
    Returns a summary string or None if AI is unavailable.
    """
    try:
        from openai import OpenAI
        client = OpenAI(base_url=endpoint, api_key="foundry-local")
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a Surface AI Factory code reviewer. "
                        "Identify violations of these best practices in the provided file: "
                        "1) WinUI3/Windows App SDK only (no Python/Electron), "
                        "2) Foundry Local SDK (no direct REST to AI endpoints), "
                        "3) NPU-first inference with demo mode fallback, "
                        "4) Zero data egress (all inference on-device), "
                        "5) Model abstraction layer (IInferenceService). "
                        "Be brief — list violations as bullet points only. "
                        "If no violations, say 'No violations detected.'"
                    ),
                },
                {
                    "role": "user",
                    "content": f"File: {filename}\n\n```\n{content[:4000]}\n```",
                },
            ],
            max_tokens=256,
            temperature=0.1,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return None


# ─── Report generation ────────────────────────────────────────────────────────

def print_report(section_results, repo_name: str, applied: dict | None = None):
    divider = "─" * 70
    print(f"\n{colour(BOLD + '=' * 70, BOLD)}")
    print(colour(f"  Surface AI Factory — Compliance Report", BOLD + CYAN))
    print(f"  Repository: {repo_name}")
    print(f"  Generated:  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{divider}\n")

    pass_count = warn_count = fail_count = 0

    for section in section_results:
        status_col = STATUS_COLOUR[section.status]
        print(colour(f"{STATUS_ICON[section.status]}  {section.section}", status_col))
        for f in section.findings:
            icon = STATUS_ICON[f.severity]
            line = f"    {icon} [{f.rule}] {f.message}"
            print(colour(line, STATUS_COLOUR[f.severity]))
            if f.fix and f.severity != PASS:
                print(f"       → {f.fix}")
            if f.severity == PASS:
                pass_count += 1
            elif f.severity == WARN:
                warn_count += 1
            else:
                fail_count += 1
        print()

    print(divider)
    total = pass_count + warn_count + fail_count
    score = int((pass_count / total) * 100) if total else 0
    print(colour(f"  Score: {score}%  |  PASS: {pass_count}  WARN: {warn_count}  FAIL: {fail_count}", BOLD))
    print(divider)

    if applied:
        print(colour(f"\n  Automated Remediations Applied:", BOLD + CYAN))
        for category, actions in applied.items():
            print(f"\n  [{category.upper()}]")
            for action in actions:
                print(f"    • {action}")

    print()


def save_report(section_results, repo_name: str, applied: dict | None,
                output_path: Path, guardrails: list | None = None):
    """Save a JSON compliance report to disk."""
    report = {
        "repository": repo_name,
        "generated": datetime.now(timezone.utc).isoformat(),
        "sections": [
            {
                "section": s.section,
                "status": s.status,
                "findings": [
                    {
                        "severity": f.severity,
                        "rule": f.rule,
                        "message": f.message,
                        "file": f.file,
                        "fix": f.fix,
                        "auto_fixed": f.auto_fix,
                    }
                    for f in s.findings
                ],
            }
            for s in section_results
        ],
        "remediations_applied": applied or {},
        "guardrail_escalations": guardrails or [],
    }
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"  [REPORT] Saved to {output_path}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Surface AI Factory Best Practices Harness Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(__doc__ or ""),
    )
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--repo", help="GitHub repo URL to process")
    source.add_argument("--new", metavar="REQUEST",
                        help="Build a new app/agent/solution from this request")
    source.add_argument("--idea", metavar="IDEA",
                        help="EVENT MODE: turn a local app idea into a bulletproof, Zava-branded "
                             "NPU demo app (model selection, build, warm-up, Go/No-Go)")
    source.add_argument("--preflight", metavar="APP_DIR",
                        help="EVENT MODE: Go/No-Go check for a generated app on this device "
                             "(caches models, builds, warms the NPU, records replay)")
    p.add_argument("--name", help="Repository/app name for --new or --idea mode")
    p.add_argument("--model", metavar="ALIAS",
                   help="--idea: pin a Foundry Local alias (must have an NPU build), e.g. phi-4-mini")
    p.add_argument("--plan-only", action="store_true",
                   help="--idea: show the idea analysis and model plan, build nothing")
    p.add_argument("--no-ai-assist", action="store_true",
                   help="--idea: do not use the local NPU model to tailor the app spec")
    p.add_argument("--assist-timeout", type=int, default=25,
                   help="--idea: seconds to wait for the optional local AI assist (default 25)")
    p.add_argument("--offline-catalog", action="store_true",
                   help="--idea: use the bundled Foundry catalog snapshot instead of the live service")
    p.add_argument("--skip-preflight", action="store_true",
                   help="--idea: generate and audit only; run --preflight later")
    p.add_argument("--warmup-timeout", type=int, default=780,
                   help="Seconds allowed for the in-app warm-up (default 780; the whole --idea run "
                        "is held to --budget-minutes)")
    p.add_argument("--budget-minutes", type=int, default=25,
                   help="--idea: hard time budget for the whole build (default 25). Slow steps are "
                        "skipped or shortened to stay inside it.")
    p.add_argument("--full-rehearsal", action="store_true",
                   help="--preflight: rehearse every sample in every mode and warm the backup model "
                        "(slower; the default rehearses one sample per mode)")
    p.add_argument("--no-download", action="store_true",
                   help="--preflight: report uncached models instead of downloading them")
    p.add_argument("--overwrite", action="store_true",
                   help="--idea: replace an existing app folder with the same name (rehearsals)")
    p.add_argument("--launch", action="store_true",
                   help="--idea: launch the app when preflight says GO")
    p.add_argument("--target-org", default="AI-Runs-Local", help="GitHub org for output")
    p.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN", ""), help="GitHub PAT")
    p.add_argument("--work-dir", default=None,
                   help="Local working directory. --idea default: %%USERPROFILE%%\\ZavaLiveBuilds "
                        "(on this device, outside OneDrive). Other modes: .harness-workspace")
    p.add_argument("--report-only", action="store_true", help="Audit only, no fixes or push")
    p.add_argument("--no-push", action="store_true", help="Apply fixes but don't push")
    p.add_argument("--ai-endpoint", default="http://localhost:5272/v1", help="Local AI endpoint")
    p.add_argument("--ai-model", default="phi-3.5-mini-instruct", help="Model for AI analysis")
    p.add_argument("--non-interactive", action="store_true",
                   help="Never prompt. Unsatisfiable guardrails default to the safest "
                        "route (scaffold, then manual follow-up) and are reported loudly.")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


def main():
    # Reports use box-drawing characters; Windows consoles default to cp1252 when piped.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = parse_args()

    if args.idea or args.preflight:
        from event_flow import run_event_build, run_preflight_only
        sys.exit(run_event_build(args) if args.idea else run_preflight_only(args))

    if args.work_dir is None:
        args.work_dir = ".harness-workspace"

    print(colour(f"\nSurface AI Factory -- Best Practices Harness Agent", BOLD + CYAN))
    print(colour(f"{'=' * 55}", CYAN))

    build_request = args.new.strip() if args.new else ""
    repo_url = args.repo.strip() if args.repo else ""
    repo_name = (
        slugify_repo_name(args.name)
        if args.name else
        (repo_name_from_url(repo_url) if repo_url else slugify_repo_name(build_request))
    )
    work_dir  = Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Acquire or create source ────────────────────────────────────────
    if repo_url:
        print(f"\n{colour('[1/4] Cloning repository', BOLD)}")
        repo_root = clone_repo(repo_url, work_dir)
        push_branch = get_default_branch(repo_root)
        print(f"  [OK] Cloned to {repo_root}")
    else:
        print(f"\n{colour('[1/4] Building new solution scaffold', BOLD)}")
        repo_root = create_new_solution(
            request=build_request,
            repo_name=repo_name,
            work_dir=work_dir,
            model_name=args.ai_model,
        )
        push_branch = "main"
        print(f"  [OK] Created new guarded solution at {repo_root}")

    # ── 2. Compliance check ────────────────────────────────────────────────
    print(f"\n{colour('[2/4] Running compliance checks', BOLD)}")
    section_results = run_all_checks(repo_root)

    # Optional AI-assisted per-file analysis
    if args.verbose and not args.report_only:
        print(f"  [AI] Attempting AI-assisted analysis via {args.ai_endpoint}")
        cs_files = list(repo_root.rglob("*.cs"))[:5]  # limit to top 5 C# files
        for f in cs_files:
            suggestion = ai_analyse_file(f.read_text(errors="ignore"), f.name, args.ai_endpoint, args.ai_model)
            if suggestion and "No violations" not in suggestion:
                print(f"\n  [AI analysis: {f.name}]\n  {suggestion}\n")

    # ── 3. Apply remediations ──────────────────────────────────────────────
    applied: dict = {}
    registry = GuardrailRegistry(interactive=not args.non_interactive)

    if not args.report_only:
        print(f"\n{colour('[3/4] Applying automated remediations', BOLD)}")
        print(colour("  [P0] Branding & attribution first (Zava + Surface AI Factory watermark)", CYAN))
        applied = run_all_remediations(repo_root, repo_name, registry)

        # Any guardrail we could not satisfy automatically is escalated to the
        # user here — nothing is ever skipped silently.
        if registry.escalations:
            registry.resolve_all()
            if registry.aborted:
                print(colour("\n  Run aborted at user request. Nothing was pushed.", RED))
                registry.print_outcome()
                return
            follow_up = apply_guardrail_decisions(repo_root, registry)
            for category, actions in follow_up.items():
                applied.setdefault(category, []).extend(actions)

        if not applied:
            print("  [OK] No remediations needed — repository already compliant.")

        # Re-run the branding checks so the published report reflects the
        # post-remediation state rather than the state we found on clone.
        section_results = run_all_checks(repo_root)

        # Save compliance report into the repo so it travels with it
        report_path = repo_root / ".surface-ai-factory" / "compliance-report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        save_report(section_results, repo_name, applied, report_path, registry.summary())
        applied.setdefault("meta", []).append("Generated .surface-ai-factory/compliance-report.json")
    else:
        print(f"\n{colour('[3/4] Report-only mode — skipping remediations', YELLOW)}")

    # ── 4. Push to target org ──────────────────────────────────────────────
    output_url = None
    if not args.report_only and not args.no_push:
        print(f"\n{colour('[4/4] Pushing to github.com/' + args.target_org, BOLD)}")
        token = args.github_token
        if not token:
            print(colour("  [ERROR] No GitHub token provided. Set --github-token or GITHUB_TOKEN env var.", RED))
            print(colour("  [INFO]  Skipping push. Run with --no-push to suppress this warning.", YELLOW))
        else:
            try:
                if repo_url:
                    # Get description from original repo if possible
                    import requests as req
                    orig = req.get(f"https://api.github.com/repos/{repo_url.rstrip('/').split('github.com/')[-1]}",
                                   timeout=10).json()
                    description = orig.get("description", "") or f"Surface AI Factory — {repo_name}"
                else:
                    description = f"Surface AI Factory — {build_request[:120]}"
            except Exception:
                description = f"Surface AI Factory — {repo_name}"

            ensure_target_repo(token, args.target_org, repo_name, description)
            output_url = push_to_target(
                repo_root, token, args.target_org, repo_name,
                branch=push_branch,
                commit_message="chore: apply Surface AI Factory best practices [harness-agent]"
            )
    elif args.no_push:
        print(f"\n{colour('[4/4] --no-push flag set — skipping GitHub upload', YELLOW)}")
    else:
        print(f"\n{colour('[4/4] Report-only mode — skipping push', YELLOW)}")

    # ── Print final report ─────────────────────────────────────────────────
    print_report(section_results, repo_name, applied if not args.report_only else None)

    registry.print_outcome()

    if output_url:
        print(colour(f"  Published: {output_url}", BOLD + GREEN))
        print()


if __name__ == "__main__":
    main()
