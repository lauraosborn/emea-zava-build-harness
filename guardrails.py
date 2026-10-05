"""
Surface AI Factory — Guardrail escalation.

A *guardrail* is a non-negotiable requirement from the Surface AI Factory
best-practices document. The harness always attempts to satisfy a guardrail
automatically. When it genuinely cannot, it must NOT silently skip it —
it raises an escalation and asks the user what the next best route is.

Priority ordering is defined here: branding guardrails (Section 1) are
P0 and are always evaluated and remediated before anything else.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field


# ─── Priority tiers ───────────────────────────────────────────────────────────

P0 = "P0"   # Must never be skipped silently — branding, attribution, consent gates
P1 = "P1"   # Strongly required — architecture, inference, security
P2 = "P2"   # Recommended — docs, polish

PRIORITY_ORDER = {P0: 0, P1: 1, P2: 2}


@dataclass
class Option:
    """One route the user can choose when a guardrail cannot be auto-applied."""
    key: str
    label: str
    detail: str = ""


@dataclass
class Escalation:
    """
    Raised when a guardrail cannot be satisfied automatically.

    The harness never decides on the user's behalf for a P0 guardrail —
    it presents the situation and the available routes, and asks.
    """
    guardrail: str                  # rule id, e.g. "watermark-app-ui"
    section: str                    # doc section, e.g. "1. Branding & User Experience"
    requirement: str                # what the doc demands, quoted plainly
    reason: str                     # why it could not be applied automatically
    options: list[Option] = field(default_factory=list)
    priority: str = P0
    resolution: str | None = None   # key of the option chosen
    resolution_label: str = ""
    note: str = ""                  # free-text answer if the user chose "other"

    @property
    def resolved(self) -> bool:
        return self.resolution is not None


# ─── Standard option sets ─────────────────────────────────────────────────────

def branding_options(target_desc: str) -> list[Option]:
    """Routes offered when a branding guardrail cannot be applied automatically."""
    return [
        Option(
            "scaffold",
            f"Scaffold {target_desc}",
            "Create the missing file/surface from a Surface AI Factory template so the "
            "guardrail is satisfied. Recommended — keeps the repo compliant.",
        ),
        Option(
            "manual",
            "Flag for manual follow-up",
            "Record the gap in the compliance report and BRANDING-TODO.md, and continue. "
            "The repo will be published as non-compliant on this guardrail.",
        ),
        Option(
            "waive",
            "Waive this guardrail for this repo",
            "Record an explicit, justified waiver. Use only when the requirement genuinely "
            "does not apply (e.g. a headless library with no UI surface).",
        ),
        Option(
            "abort",
            "Abort the run",
            "Stop now and change nothing further. Nothing is pushed.",
        ),
    ]


# ─── Registry ─────────────────────────────────────────────────────────────────

def model_not_cached_options(model_id: str, size_gb: float, alternative: str | None) -> list[Option]:
    """Routes when the best model for the idea is not on the device yet."""
    options = [
        Option(
            "download",
            f"Download {model_id} during preflight",
            f"About {size_gb:.1f} GB. Recommended when you are on reliable Wi-Fi well before "
            "the session. Preflight fails loudly if the download does not complete.",
        ),
    ]
    if alternative:
        options.append(Option(
            "cached",
            f"Use {alternative} instead (already cached)",
            "Nothing downloads. Best choice on venue Wi-Fi or when you are short on time.",
        ))
    options += [
        Option("manual", "Flag for manual follow-up",
               "Build anyway and record the gap. The app will use the replay safety net "
               "until the model is cached."),
        Option("abort", "Abort the run", "Stop now. Nothing is built."),
    ]
    return options


def capability_gap_options(capability: str, best_available: str | None) -> list[Option]:
    """Routes when the idea needs a modality with no NPU model on this device."""
    return [
        Option(
            "text-first",
            "Build a text-first demo on the NPU",
            f"Keep every model on the NPU. The {capability} input is represented as text "
            "(for example a pasted transcript or description). Recommended for stage reliability.",
        ),
        Option(
            "manual",
            f"Flag {capability} support for manual follow-up",
            f"Build the NPU text experience now and record that {capability} needs "
            f"{best_available or 'a non-NPU model'}, which must stay consent-gated.",
        ),
        Option("abort", "Abort the run", "Stop now. Nothing is built."),
    ]


def logo_options() -> list[Option]:
    """Routes offered when the Zava logo asset is missing and cannot be fabricated."""
    return [
        Option(
            "supply",
            "Point me at the Zava logo file",
            "Provide a local path to the official Zava logo; it will be copied into "
            "Assets/zava-logo.png. Recommended — this is the only fully compliant route.",
        ),
        Option(
            "placeholder",
            "Generate a text-based placeholder logo",
            "Create a simple SVG wordmark reading 'Zava' so the UI renders and the "
            "layout is correct. Must be replaced with the official asset before release.",
        ),
        Option(
            "manual",
            "Flag for manual follow-up",
            "Leave the asset missing, record it in BRANDING-TODO.md, and continue.",
        ),
        Option(
            "abort",
            "Abort the run",
            "Stop now and change nothing further. Nothing is pushed.",
        ),
    ]


class GuardrailRegistry:
    """
    Collects escalations during a run and prompts the user to resolve them.

    In non-interactive mode nothing is silently dropped: every unresolved
    escalation is defaulted to the *safest* route (never 'waive') and is
    reported loudly so a human can act on it.
    """

    def __init__(self, interactive: bool = True, default_route: str = "scaffold"):
        self.interactive = interactive
        self.default_route = default_route
        self.escalations: list[Escalation] = []
        self.aborted = False

    # -- collection -----------------------------------------------------------

    def raise_escalation(self, esc: Escalation) -> Escalation:
        self.escalations.append(esc)
        return esc

    def branding(self, guardrail: str, requirement: str, reason: str,
                 target_desc: str) -> Escalation:
        """Convenience helper for Section 1 branding guardrails (always P0)."""
        return self.raise_escalation(Escalation(
            guardrail=guardrail,
            section="1. Branding & User Experience",
            requirement=requirement,
            reason=reason,
            options=branding_options(target_desc),
            priority=P0,
        ))

    # -- resolution -----------------------------------------------------------

    @property
    def unresolved(self) -> list[Escalation]:
        return [e for e in self.escalations if not e.resolved]

    def _sorted_pending(self) -> list[Escalation]:
        return sorted(self.unresolved, key=lambda e: PRIORITY_ORDER.get(e.priority, 9))

    def resolve_all(self) -> None:
        """Prompt for every pending escalation, highest priority first."""
        pending = self._sorted_pending()
        if not pending:
            return

        print()
        print("=" * 70)
        print(f"  GUARDRAIL ESCALATION — {len(pending)} item(s) need a decision")
        print("=" * 70)
        print(
            "  The harness could not satisfy the following guardrail(s)\n"
            "  automatically. Nothing has been skipped silently.\n"
        )

        for esc in pending:
            self._resolve_one(esc)
            if self.aborted:
                return

    def _resolve_one(self, esc: Escalation) -> None:
        print("-" * 70)
        print(f"  [{esc.priority}] {esc.section}")
        print(f"  Guardrail : {esc.guardrail}")
        print(f"  Requires  : {esc.requirement}")
        print(f"  Blocked by: {esc.reason}")
        print()

        if not self.interactive:
            choice = self._safe_default(esc)
            esc.resolution = choice.key
            esc.resolution_label = choice.label
            esc.note = "auto-selected (non-interactive mode)"
            print(f"  [NON-INTERACTIVE] Defaulting to: {choice.label}")
            print("  Re-run without --non-interactive to choose a different route.")
            print()
            return

        for i, opt in enumerate(esc.options, 1):
            print(f"    {i}. {opt.label}")
            if opt.detail:
                for line in _wrap(opt.detail, 62):
                    print(f"       {line}")
        print(f"    {len(esc.options) + 1}. Something else (describe it)")
        print()

        choice = self._read_choice(esc)
        if choice is None:  # EOF / no TTY
            fallback = self._safe_default(esc)
            esc.resolution = fallback.key
            esc.resolution_label = fallback.label
            esc.note = "auto-selected (no interactive input available)"
            print(f"  [NO INPUT] Defaulting to: {fallback.label}\n")
            return

        if choice == "other":
            note = input("  Describe the route you want: ").strip()
            esc.resolution = "other"
            esc.resolution_label = "User-specified route"
            esc.note = note
            print(f"  [RECORDED] {note}\n")
            return

        esc.resolution = choice.key
        esc.resolution_label = choice.label
        if choice.key == "abort":
            self.aborted = True
            print("  [ABORT] Stopping run at user request.\n")
        elif choice.key == "waive":
            reason = input("  Justification for the waiver (required): ").strip()
            esc.note = reason or "no justification given"
            print(f"  [WAIVED] {esc.note}\n")
        elif choice.key == "supply":
            path = input("  Path to the Zava logo file: ").strip().strip('"')
            esc.note = path
            print(f"  [RECORDED] Will copy logo from: {path}\n")
        else:
            print(f"  [SELECTED] {choice.label}\n")

    def _read_choice(self, esc: Escalation):
        max_n = len(esc.options) + 1
        for _ in range(5):
            try:
                raw = input(f"  Choose 1-{max_n}: ").strip()
            except (EOFError, KeyboardInterrupt):
                return None
            if not raw:
                continue
            if raw.isdigit():
                n = int(raw)
                if 1 <= n <= len(esc.options):
                    return esc.options[n - 1]
                if n == max_n:
                    return "other"
            print(f"  Please enter a number between 1 and {max_n}.")
        return None

    @staticmethod
    def _safe_default(esc: Escalation) -> Option:
        """
        Safest non-interactive route: prefer creating the missing surface, then a
        zero-download cached model, then downloading, then a placeholder, then manual
        follow-up. Never auto-waive, never auto-abort, and never auto-select a route
        that needs user-supplied input.
        """
        by_key = {o.key: o for o in esc.options}
        for key in ("scaffold", "cached", "download", "text-first", "placeholder", "manual"):
            if key in by_key:
                return by_key[key]
        return esc.options[0]

    def event(self, guardrail: str, requirement: str, reason: str,
              options: list[Option], priority: str = P1) -> Escalation:
        """Escalation for Section 11 live-event readiness guardrails."""
        return self.raise_escalation(Escalation(
            guardrail=guardrail,
            section="11. Live Event Readiness",
            requirement=requirement,
            reason=reason,
            options=options,
            priority=priority,
        ))

    def logo(self, guardrail: str, requirement: str, reason: str) -> Escalation:
        """Escalation for the Zava logo asset (P0, Section 1.1)."""
        return self.raise_escalation(Escalation(
            guardrail=guardrail,
            section="1. Branding & User Experience",
            requirement=requirement,
            reason=reason,
            options=logo_options(),
            priority=P0,
        ))

    # -- reporting ------------------------------------------------------------

    def summary(self) -> list[dict]:
        return [
            {
                "guardrail": e.guardrail,
                "section": e.section,
                "priority": e.priority,
                "requirement": e.requirement,
                "reason": e.reason,
                "resolution": e.resolution,
                "resolution_label": e.resolution_label,
                "note": e.note,
            }
            for e in self.escalations
        ]

    def scaffold_approved(self, guardrail: str) -> bool:
        """True if the user chose to scaffold for this guardrail."""
        for e in self.escalations:
            if e.guardrail == guardrail:
                return e.resolution == "scaffold"
        return False

    def print_outcome(self) -> None:
        if not self.escalations:
            return
        print()
        print("=" * 70)
        print("  GUARDRAIL DECISIONS")
        print("=" * 70)
        for e in self.escalations:
            state = e.resolution_label or "UNRESOLVED"
            print(f"  [{e.priority}] {e.guardrail}: {state}")
            if e.note:
                print(f"        note: {e.note}")
        outstanding = [e for e in self.escalations
                       if e.resolution in ("manual", "waive", "other", None)]
        if outstanding:
            print()
            print(f"  {len(outstanding)} guardrail(s) still need human action —")
            print("  see BRANDING-TODO.md in the repo and the compliance report.")
        print()


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines
