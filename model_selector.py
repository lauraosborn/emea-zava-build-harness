"""
Zava Live Build -- idea analysis and model selection.

Turns a plain-English app idea into:
  * the capabilities it needs (chat, code, reasoning, speech, vision, ...)
  * a ModelPlan: a pinned primary NPU model, a small NPU backup, and a
    plain-English rationale the presenter can read out on stage.

Selection is deterministic and explainable. It only ever picks models that
publish an NPU build on THIS device's Foundry Local catalog. Models that are
already cached are strongly preferred, so nothing has to download on stage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from foundry_catalog import Catalog, ModelVariant

CHAT = "chat-completion"

# Keyword cues -> capability. Kept simple so the presenter can explain them.
CAPABILITY_CUES: dict[str, tuple[str, ...]] = {
    "code": ("code", "coding", "script", "sql", "regex", "developer", "programming",
             "powershell", "python", "javascript", "unit test", "refactor", "bug"),
    "reasoning": ("reason", "math", "calculat", "logic", "root cause", "troubleshoot",
                  "diagnos", "step-by-step", "step by step", "plan ", "planning", "decision"),
    "speech": ("transcri", "speech", "voice", "audio", "dictat", "spoken", "microphone",
               "call recording", "podcast"),
    "vision": ("image", "photo", "picture", "camera", "screenshot", "scan", "visual",
               "diagram", "video"),
    "documents": ("document", "contract", "report", "policy", "manual", "pdf", "notes",
                  "email", "minutes", "summar"),
    "multilingual": ("translat", "multilingual", "language", "spanish", "french", "german",
                     "japanese", "chinese", "arabic"),
    "agentic": ("agent", "tool", "workflow", "automate", "action", "orchestrat"),
    "privacy": ("patient", "medical", "health", "legal", "confidential", "sensitive",
                "privacy", "private", "hr ", "financial", "customer data"),
}

# Preferred NPU aliases per primary capability, best first. Small, fast,
# instruction-tuned models win on stage: low time-to-first-token matters more
# than benchmark scores when an audience is watching.
PREFERENCES: dict[str, tuple[str, ...]] = {
    "code": ("qwen2.5-coder-1.5b", "qwen2.5-coder-0.5b", "qwen2.5-coder-7b", "phi-4-mini"),
    "reasoning": ("phi-4-mini", "qwen2.5-1.5b", "phi-4-mini-reasoning", "deepseek-r1-7b"),
    "multilingual": ("qwen2.5-1.5b", "phi-4-mini", "qwen2.5-7b", "qwen2.5-0.5b"),
    "documents": ("phi-4-mini", "qwen2.5-1.5b", "phi-3-mini-4k", "qwen2.5-0.5b"),
    "general": ("phi-4-mini", "qwen2.5-1.5b", "phi-3-mini-4k", "qwen2.5-0.5b"),
}

BACKUP_PREFERENCES = ("qwen2.5-0.5b", "qwen2.5-1.5b", "qwen2.5-coder-0.5b", "phi-3-mini-4k")

# Models above this size are slow to compile/load for a live demo. They are
# still allowed, but only when nothing smaller fits.
STAGE_SIZE_BUDGET_MB = 3072

# Reasoning-distilled models stream long hidden "thinking" before answering,
# which looks like a hang on stage.
STAGE_UNFRIENDLY = ("reasoning", "deepseek-r1")

SPEECH_TASKS = ("automatic-speech-recognition", "speech-to-text", "audio-transcription")
VISION_TASKS = ("vision-language-chat", "image-to-text")


@dataclass
class IdeaAnalysis:
    idea: str
    capabilities: list[str]
    primary: str
    cues: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class ModelChoice:
    alias: str
    model_id: str
    device: str
    execution_provider: str
    size_mb: int
    license: str
    cached: bool
    tool_calling: bool
    role: str                     # primary | backup
    reasons: list[str] = field(default_factory=list)


@dataclass
class CapabilityGap:
    capability: str
    detail: str
    best_available: str | None    # e.g. "whisper-small (CPU)"


@dataclass
class ModelPlan:
    analysis: IdeaAnalysis
    catalog_source: str
    npu_execution_provider: str | None
    primary: ModelChoice | None
    backup: ModelChoice | None
    gaps: list[CapabilityGap] = field(default_factory=list)
    considered: list[str] = field(default_factory=list)

    @property
    def models_to_cache(self) -> list[ModelChoice]:
        return [m for m in (self.primary, self.backup) if m and not m.cached]

    def to_dict(self) -> dict:
        return {
            "idea": self.analysis.idea,
            "capabilities": self.analysis.capabilities,
            "primaryCapability": self.analysis.primary,
            "catalogSource": self.catalog_source,
            "npuExecutionProvider": self.npu_execution_provider,
            "primary": asdict(self.primary) if self.primary else None,
            "backup": asdict(self.backup) if self.backup else None,
            "gaps": [asdict(g) for g in self.gaps],
            "considered": self.considered,
        }


# ── Idea analysis ───────────────────────────────────────────────────────────

def analyse_idea(idea: str) -> IdeaAnalysis:
    text = f" {idea.lower()} "
    cues: dict[str, list[str]] = {}
    for capability, words in CAPABILITY_CUES.items():
        hits = [w.strip() for w in words if w in text]
        if hits:
            cues[capability] = hits

    capabilities = ["chat"] + [c for c in CAPABILITY_CUES if c in cues]
    for primary in ("code", "reasoning", "multilingual", "documents"):
        if primary in cues:
            break
    else:
        primary = "general"
    return IdeaAnalysis(idea=idea, capabilities=capabilities, primary=primary, cues=cues)


# ── Selection ───────────────────────────────────────────────────────────────

def _npu_chat_variants(catalog: Catalog) -> list[ModelVariant]:
    return [v for v in catalog.npu(CHAT)]


def _best_variant_for_alias(catalog: Catalog, alias: str) -> ModelVariant | None:
    """Highest-version NPU chat build of an alias, preferring a cached one."""
    candidates = [v for v in catalog.by_alias(alias) if v.device == "NPU" and v.task == CHAT]
    if not candidates:
        return None
    return sorted(candidates, key=lambda v: (v.cached, _version_num(v)), reverse=True)[0]


def _version_num(v: ModelVariant) -> int:
    try:
        return int(v.version)
    except ValueError:
        return 0


def _stage_friendly(v: ModelVariant) -> bool:
    return not any(word in v.alias.lower() for word in STAGE_UNFRIENDLY)


def _reasons(v: ModelVariant, analysis: IdeaAnalysis, role: str, rank: int | None,
             catalog: Catalog) -> list[str]:
    ep = v.execution_provider.replace("ExecutionProvider", "") or "NPU"
    reasons = [f"Publishes an NPU build on this device ({ep} execution provider)."]
    if role == "primary":
        if rank is not None:
            fit = analysis.primary if analysis.primary != "general" else "general assistant"
            reasons.append(f"Ranked #{rank + 1} for '{fit}' workloads in the stage playbook.")
        else:
            reasons.append("Best remaining NPU chat model after preferred options were ruled out.")
    else:
        reasons.append("Small and fast: keeps the demo alive if the primary model fails to load.")
    reasons.append(
        "Already cached on this device: nothing downloads on stage."
        if v.cached else
        f"Not cached yet ({v.size_gb} GB): preflight will download it before the show."
    )
    reasons.append(f"{v.size_gb} GB on disk, within the {STAGE_SIZE_BUDGET_MB / 1024:.1f} GB stage budget."
                   if v.size_mb <= STAGE_SIZE_BUDGET_MB else
                   f"{v.size_gb} GB: larger than the stage budget, expect a longer first load.")
    if v.license:
        reasons.append(f"License: {v.license}.")
    if "agentic" in analysis.capabilities and v.tool_calling:
        reasons.append("Supports tool calling for agent-style flows.")
    return reasons


def _choice(v: ModelVariant, role: str, reasons: list[str]) -> ModelChoice:
    return ModelChoice(alias=v.alias, model_id=v.id, device=v.device,
                       execution_provider=v.execution_provider, size_mb=v.size_mb,
                       license=v.license, cached=v.cached, tool_calling=v.tool_calling,
                       role=role, reasons=reasons)


def select_models(analysis: IdeaAnalysis, catalog: Catalog,
                  pin_alias: str | None = None) -> ModelPlan:
    considered: list[str] = []
    primary_variant: ModelVariant | None = None
    rank: int | None = None

    if pin_alias:
        primary_variant = _best_variant_for_alias(catalog, pin_alias)
        considered.append(f"{pin_alias}: pinned by presenter"
                          + ("" if primary_variant else " (no NPU build, ignored)"))

    if primary_variant is None:
        prefs = PREFERENCES.get(analysis.primary, PREFERENCES["general"])
        candidates = []
        for i, alias in enumerate(prefs):
            v = _best_variant_for_alias(catalog, alias)
            if v is None:
                considered.append(f"{alias}: no NPU build in this catalog")
                continue
            if not _stage_friendly(v):
                considered.append(f"{alias}: reasoning-distilled, long hidden 'thinking' looks like a hang on stage")
                continue
            candidates.append((i, v))
        # Best fit wins (within the stage size budget). If it is not cached, the harness
        # raises a guardrail so the presenter decides: download it, or use a cached model.
        candidates.sort(key=lambda iv: (iv[1].size_mb > STAGE_SIZE_BUDGET_MB, iv[0]))
        if candidates:
            rank, primary_variant = candidates[0]
            for i, v in candidates[1:]:
                considered.append(f"{v.alias}: viable alternative "
                                  f"({'cached' if v.cached else 'not cached'}, {v.size_gb} GB)")

    if primary_variant is None:
        pool = [v for v in _npu_chat_variants(catalog) if _stage_friendly(v)]
        pool.sort(key=lambda v: (not v.cached, v.size_mb))
        primary_variant = pool[0] if pool else None

    backup_variant: ModelVariant | None = None
    for alias in BACKUP_PREFERENCES:
        if primary_variant and alias == primary_variant.alias:
            continue
        v = _best_variant_for_alias(catalog, alias)
        if v and (v.cached or backup_variant is None):
            backup_variant = v
            if v.cached:
                break

    plan = ModelPlan(
        analysis=analysis,
        catalog_source=catalog.source,
        npu_execution_provider=catalog.npu_execution_provider,
        primary=_choice(primary_variant, "primary",
                        _reasons(primary_variant, analysis, "primary", rank, catalog)) if primary_variant else None,
        backup=_choice(backup_variant, "backup",
                       _reasons(backup_variant, analysis, "backup", None, catalog)) if backup_variant else None,
        considered=considered,
    )
    plan.gaps = _capability_gaps(analysis, catalog)
    return plan


def best_cached_alternative(catalog: Catalog, exclude_alias: str | None = None,
                            analysis: IdeaAnalysis | None = None) -> ModelVariant | None:
    """Best cached, stage-friendly NPU chat model: playbook order for the idea first, then size."""
    pool = [v for v in _npu_chat_variants(catalog)
            if v.cached and _stage_friendly(v) and v.size_mb <= STAGE_SIZE_BUDGET_MB
            and (exclude_alias is None or v.alias != exclude_alias)]
    if not pool:
        return None
    prefs = PREFERENCES.get(analysis.primary if analysis else "general", PREFERENCES["general"])
    rank = {alias: i for i, alias in enumerate(prefs)}
    return min(pool, key=lambda v: (rank.get(v.alias, len(prefs)), -v.size_mb))


def _capability_gaps(analysis: IdeaAnalysis, catalog: Catalog) -> list[CapabilityGap]:
    gaps = []
    for capability, tasks in (("speech", SPEECH_TASKS), ("vision", VISION_TASKS)):
        if capability not in analysis.capabilities:
            continue
        variants = [v for v in catalog.variants if v.task in tasks]
        if any(v.device == "NPU" for v in variants):
            continue
        best = sorted(variants, key=lambda v: (v.device != "CPU", not v.cached, v.size_mb))
        gaps.append(CapabilityGap(
            capability=capability,
            detail=(f"This idea needs {capability}, but no {capability} model in this device's "
                    "Foundry Local catalog publishes an NPU build."),
            best_available=f"{best[0].alias} ({best[0].device})" if best else None,
        ))
    return gaps


# ── App specification ───────────────────────────────────────────────────────

FILLER = {"a", "an", "the", "build", "create", "make", "me", "app", "application", "that",
          "which", "for", "to", "local", "ai", "an", "on", "device", "npu", "powered",
          "using", "with", "of", "and", "i", "want", "we", "need", "who", "helps", "help",
          "lets", "let", "can", "will", "tool", "demo", "simple", "small", "quick"}


@dataclass
class AppSpec:
    title: str
    tagline: str
    system_prompt: str
    input_label: str
    sample_prompts: list[str]
    generated_by: str = "rules"
    industry: str = "general"
    industry_name: str = "General"
    organisation: str = "Zava"
    theme: dict = field(default_factory=dict)
    modes: list[dict] = field(default_factory=list)
    context_title: str = ""
    context_fields: list[dict] = field(default_factory=list)
    local_drivers: list[dict] = field(default_factory=list)
    knowledge: bool = False
    knowledge_pack: str | None = None
    languages: list[str] = field(default_factory=list)
    brand: dict = field(default_factory=dict)
    prototype: dict = field(default_factory=dict)
    workspace: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


_NAME_PREFIX = re.compile(r"^\s*(?:local\s+ai\s+|on-?device\s+ai\s+|ai\s+)?([A-Z][\w&' -]{2,40}?)\s*[:\n]",
                          re.IGNORECASE)


def title_from_idea(idea: str) -> str:
    named = _NAME_PREFIX.match(idea)
    if named:
        return named.group(1).strip()
    words = re.findall(r"[A-Za-z0-9+'-]+", idea)
    core = [w for w in words if w.lower() not in FILLER]
    title = " ".join(w if w.isupper() else w.capitalize() for w in core[:3]) or "Local Assistant"
    if not re.search(r"(assistant|agent|studio|copilot|coach|helper|bot|builder|analy[sz]er|concierge)$",
                     title, re.IGNORECASE):
        title += " Assistant"
    return title


def tagline_from_idea(idea: str) -> str:
    """The first descriptive sentence of the idea, without hardware talk. The app says what it does."""
    raw = _NAME_PREFIX.sub("", idea, count=1) if _NAME_PREFIX.match(idea) else idea
    text = re.sub(r"\s+", " ", raw).strip()
    first = re.split(r"(?<=[.!?])\s|\s[-–]\s", text, maxsplit=1)[0].strip().rstrip(".")
    first = re.sub(r"\b(on|using|via) (the )?NPU\b|\bNPU[- ]powered\b", "", first, flags=re.IGNORECASE)
    first = re.sub(r"\s{2,}", " ", first).strip(" ,")
    if len(first) > 170:
        first = first[:167].rsplit(" ", 1)[0] + "..."
    return first[:1].upper() + first[1:] if first else "Your on-device assistant"


_SAMPLE_INPUTS = {
    "code": [
        "Write a PowerShell function that lists the 5 largest files in a folder.",
        "Explain what this regex does: ^(?=.*\\d)(?=.*[a-z]).{8,}$",
        "Refactor this into clean C#: for(int i=0;i<list.Count;i++){if(list[i]!=null){Console.WriteLine(list[i]);}}",
    ],
    "reasoning": [
        "A field engineer reports the line stops every 40 minutes and restarts after 2. List likely causes and the first three checks.",
        "We have 3 technicians and 7 jobs today across two sites. Suggest a fair plan and explain the trade-offs.",
        "Walk me through deciding between repairing or replacing a 6-year-old laptop fleet.",
    ],
    "multilingual": [
        "Translate into French and Spanish: 'Your order has shipped and will arrive on Thursday.'",
        "Summarise this customer note in English: 'El equipo no enciende después de la actualización.'",
        "Write a polite out-of-office reply in German.",
    ],
    "documents": [
        "Summarise these notes into 3 bullet actions: Met Zava retail team. Store 12 needs new tills by Friday. Budget approved. Sam owns supplier call.",
        "Turn this into a clear customer email: 'delay on part, ETA 2 days, sorry, will call tomorrow'.",
        "Extract the key dates and owners from: 'Kick-off 3 Nov (Priya), pilot 17 Nov (Leo), go-live 1 Dec (Priya)'.",
    ],
    "general": [
        "Give me three ideas to improve our team's weekly stand-up.",
        "Rewrite this to sound friendlier: 'Your request is denied pending review.'",
        "Summarise in two sentences why on-device AI matters for privacy.",
    ],
}


LANGUAGES = ["Spanish", "French", "German", "Italian", "Portuguese", "Polish", "Japanese",
             "Mandarin Chinese", "Arabic"]

ORG_BY_INDUSTRY = {
    "hospitality": "Zava Harbour Hotel", "healthcare": "Zava Health", "retail": "Zava Stores",
    "field": "Zava Field Services", "finance": "Zava Financial", "legal": "Zava Legal",
    "education": "Zava Academy", "public": "Zava City Council", "software": "Zava Engineering",
}


def _mode_icon(mode_id: str, mode_name: str) -> str:
    text = f"{mode_id} {mode_name}".lower()
    if any(w in text for w in ("inspect", "vision", "photo", "image")):
        return "\uE722"
    if any(w in text for w in ("diagnose", "engineer", "fault", "review")):
        return "\uE9D9"
    if any(w in text for w in ("assist", "procedure", "staff", "colleague", "admin")):
        return "\uE90F"
    if any(w in text for w in ("translate", "language")):
        return "\uE8C1"
    if any(w in text for w in ("guest", "concierge", "advisor")):
        return "\uE719"
    return "\uE946"


def _mode_description(mode) -> str:
    name = mode.name.lower()
    if "inspect" in name:
        return "Capture the field observation and classify what needs attention."
    if "diagnose" in name or "engineer" in name:
        return "Use local evidence to identify likely causes and first checks."
    if "assist" in name or "staff" in name or "colleague" in name:
        return "Retrieve the procedure, checklist or next action from local guidance."
    if "translate" in name:
        return "Translate or explain approved local information in the selected language."
    if "concierge" in name or "advisor" in name:
        return "Turn context and local knowledge into a ready-to-use recommendation."
    return "Run the app's core workflow with private context held on this device."


def _default_brand(profile, org: str, knowledge: bool) -> dict:
    mark = {
        "hospitality": "\uE707", "healthcare": "\uE95E", "retail": "\uE719",
        "field": "\uE9D9", "finance": "\uE8C7", "legal": "\uE8A5",
        "education": "\uE7BE", "public": "\uE80F", "software": "\uE943",
    }.get(profile.id, "\uE946")
    domain = {
        "hospitality": "Guest operations console",
        "healthcare": "Care-team workspace",
        "retail": "Store colleague workbench",
        "field": "Field service workbench",
        "finance": "Advisor workbench",
        "legal": "Matter workbench",
        "education": "Learning support workspace",
        "public": "Resident services console",
        "software": "Engineering cockpit",
    }.get(profile.id, f"{org} workspace")
    return {
        "domain": domain,
        "markGlyph": mark,
        "workflowLabel": "Workflow",
        "heroMetric1": "Local knowledge" if knowledge else "Private context",
        "heroMetric2": "On-device answers",
        "heroMetric3": "Offline-ready",
    }


def _mode_dict(mode, org: str, samples: list[str], knowledge: bool, description: str | None = None,
               icon: str | None = None, demo_response: str | None = None) -> dict:
    grounding = (" Use ONLY the knowledge provided; if it does not cover something, say a colleague will check."
                 if knowledge else " Never claim to browse the internet or call cloud services.")
    # Small local models pad answers; these rules keep them short and stop invented extras.
    style = (" Be concise: short lines, no introduction, no filler, and no websites, phone numbers, contact "
             "details or booking advice unless the knowledge states them. Never invent names, IDs, reference "
             "numbers or dates the user did not give; write ____ for anything missing. Keep it under 150 words.")
    return {
        "id": mode.id, "name": mode.name, "inputLabel": mode.input_label, "action": mode.action,
        "outputTitle": mode.output_title,
        "systemPrompt": mode.system_core.replace("{org}", org) + grounding + style,
        "samplePrompts": samples[:3], "scopes": mode.scopes, "usesContext": mode.uses_context,
        "description": description or _mode_description(mode), "icon": icon or _mode_icon(mode.id, mode.name),
        "demoResponse": demo_response or "",
        "usesLanguage": mode.uses_language, "defaultLanguage": "Spanish",
        "topPassages": mode.top_passages if knowledge else 0,
        "diversify": mode.diversify, "minRelativeScore": mode.min_relative_score,
    }


def _similar(a: str, b: str) -> bool:
    wa, wb = (set(re.findall(r"[a-z]{4,}", s.lower())) for s in (a, b))
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.45


def _merge_samples(quotes: list[str], defaults: list[str]) -> list[str]:
    """Idea examples first, then built-in samples that are not near-copies of them."""
    merged = list(quotes)
    for s in defaults:
        if not any(_similar(s, m) for m in merged):
            merged.append(s)
    return merged[:3]


def default_app_spec(idea: str, analysis: IdeaAnalysis, name: str | None = None) -> AppSpec:
    from industry import (GENERAL, ModeSpec, TRANSLATE_CORE, detect_drivers, detect_industry,
                          is_staff_example, quoted_examples, theme_dict, wants_knowledge,
                          wants_staff_mode, wants_translation)
    from use_case_blueprints import match_blueprint

    title = name or title_from_idea(idea)
    profile = detect_industry(idea)
    blueprint = match_blueprint(idea, profile.id)
    if profile is GENERAL and analysis.primary in _SAMPLE_INPUTS:
        primary_samples = list(_SAMPLE_INPUTS[analysis.primary])
    else:
        primary_samples = list(profile.primary.samples)
    org = blueprint.organisation if blueprint else ORG_BY_INDUSTRY.get(profile.id, "Zava")
    knowledge = True if blueprint else wants_knowledge(idea, profile)
    staff = wants_staff_mode(idea, profile)

    # Examples written into the idea are the demo the presenter has in mind: use them first.
    quotes = quoted_examples(idea)
    staff_quotes = [q for q in quotes if staff and is_staff_example(q)]
    primary_quotes = [q for q in quotes if q not in staff_quotes]
    primary_samples = _merge_samples(primary_quotes, primary_samples)

    if blueprint:
        modes = []
        for bp_mode in blueprint.modes:
            mode = ModeSpec(
                bp_mode.id, bp_mode.name, bp_mode.input_label, bp_mode.action,
                bp_mode.output_title, bp_mode.system_core, list(bp_mode.samples),
                scopes=list(bp_mode.scopes), uses_context=True,
                top_passages=bp_mode.top_passages, diversify=bp_mode.diversify,
                min_relative_score=bp_mode.min_relative_score)
            modes.append(_mode_dict(mode, org, list(bp_mode.samples), knowledge,
                                    description=bp_mode.description, icon=bp_mode.icon,
                                    demo_response=bp_mode.demo_response))
    else:
        modes = [_mode_dict(profile.primary, org, primary_samples, knowledge)]
        if staff:
            staff_samples = _merge_samples(staff_quotes, profile.staff.samples)
            modes.append(_mode_dict(profile.staff, org, staff_samples, knowledge))
    if wants_translation(idea):
        translate = ModeSpec("translate", "Translate", "Text to translate, or a topic to explain", "Translate",
                             "Translation", TRANSLATE_CORE, [], scopes=["public"], uses_language=True,
                             top_passages=2)
        translate_samples = ([
            "Translate for the guest: Your room will be ready at 3pm. You can leave your bags with us and use the spa while you wait.",
            "Explain our breakfast times and the children's swimming pool times.",
            "Summarise our checkout, late checkout and luggage storage options."] if profile.id == "hospitality" else [
            "Translate: Thank you for waiting. Someone will be with you in about five minutes.",
            "Summarise our opening hours and how to contact us.",
            "Explain what happens next and how long it usually takes."])
        modes.append(_mode_dict(translate, org, translate_samples, knowledge))
        modes[-1]["systemPrompt"] = TRANSLATE_CORE.replace("{org}", org) + " Keep it under 120 words."

    privacy = "Treat everything the user shares as confidential. " if "privacy" in analysis.capabilities else ""
    system = modes[0]["systemPrompt"]
    if privacy:
        system = privacy + system
        modes[0]["systemPrompt"] = system

    return AppSpec(
        title=name or (blueprint.title if blueprint else title),
        tagline=blueprint.tagline if blueprint else tagline_from_idea(idea),
        system_prompt=system,
        input_label=modes[0]["inputLabel"],
        sample_prompts=modes[0]["samplePrompts"],
        industry=profile.id,
        industry_name=profile.name,
        organisation=org,
        theme=theme_dict(profile.theme),
        modes=modes,
        context_title=blueprint.context_title if blueprint else profile.context_title,
        context_fields=list(blueprint.context_fields) if blueprint else [
            {"key": f.key, "label": f.label, "placeholder": f.placeholder}
            for f in profile.context_fields],
        local_drivers=list(blueprint.local_drivers) if blueprint else [
            {"id": d.id, "title": d.title, "detail": d.detail}
            for d in detect_drivers(idea, profile)],
        knowledge=knowledge,
        knowledge_pack=(blueprint.knowledge_pack if blueprint else profile.knowledge_pack) if knowledge else None,
        languages=LANGUAGES if wants_translation(idea) else [],
        brand=dict(blueprint.brand) if blueprint else _default_brand(profile, org, knowledge),
        prototype=dict(blueprint.prototype) if blueprint else {},
        workspace=dict(blueprint.workspace) if blueprint else {},
    )
