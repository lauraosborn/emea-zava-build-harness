"""
Use-case blueprints for Zava Live Build.

Industry profiles provide a good default look and tone. Blueprints go one level
deeper: when an idea clearly describes a repeatable scenario, they replace the
generic industry assistant with a purpose-built app shape, sample data and a
local knowledge pack.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class BlueprintMode:
    id: str
    name: str
    input_label: str
    action: str
    output_title: str
    system_core: str
    samples: tuple[str, ...]
    description: str
    icon: str
    demo_response: str = ""
    scopes: tuple[str, ...] = ("public",)
    top_passages: int = 4
    diversify: bool = True
    min_relative_score: float = 0.2


@dataclass(frozen=True)
class Blueprint:
    id: str
    industry: str
    cues: tuple[str, ...]
    title: str
    tagline: str
    organisation: str
    context_title: str
    context_fields: tuple[dict[str, str], ...]
    modes: tuple[BlueprintMode, ...]
    knowledge_pack: str
    local_drivers: tuple[dict[str, str], ...]
    brand: dict[str, str]
    prototype: dict[str, str | bool] = field(default_factory=dict)
    workspace: dict = field(default_factory=dict)
    required_hits: int = 3
    exclusions: tuple[str, ...] = field(default_factory=tuple)


def _has(text: str, cue: str) -> bool:
    return bool(re.search(r"\b" + re.escape(cue.strip()), text))


def _score(text: str, blueprint: Blueprint) -> int:
    if any(_has(text, cue) for cue in blueprint.exclusions):
        return 0
    return sum(1 for cue in blueprint.cues if _has(text, cue))


def match_blueprint(idea: str, industry: str | None = None) -> Blueprint | None:
    text = idea.lower()
    matches = []
    for bp in BLUEPRINTS:
        if industry and bp.industry != industry:
            continue
        score = _score(text, bp)
        if score >= bp.required_hits:
            matches.append((score, bp))
    if not matches:
        return None
    return sorted(matches, key=lambda x: x[0], reverse=True)[0][1]


BLUEPRINTS: tuple[Blueprint, ...] = (
    Blueprint(
        id="manufacturing-quality",
        industry="field",
        cues=("manufactur", "factory", "quality", "inspect", "defect", "component", "surface",
              "calibration", "tool", "station", "telemetry", "fault", "production"),
        title="AI Quality Inspector",
        tagline="Inspect components, diagnose defects and retrieve factory procedures on the workstation, even offline.",
        organisation="Zava Manufacturing",
        context_title="Production context",
        context_fields=(
            {"key": "component", "label": "Component", "placeholder": "e.g. A17 upper mount bracket, batch 24-10-07"},
            {"key": "station", "label": "Station", "placeholder": "e.g. Cell 2 / Tool Station 4"},
            {"key": "telemetry", "label": "Telemetry or fault code", "placeholder": "e.g. QI-217, torque drift +8%, spindle vibration 4.8 mm/s"},
        ),
        modes=(
            BlueprintMode(
                "inspect", "Inspect", "Describe the component photo or inspection finding", "Inspect",
                "Inspection result",
                "You are a manufacturing quality inspector at {org}. Assess the described component image or inspection note against the local reference material. Identify likely defect type, affected area, confidence, immediate containment step and whether the line can continue. Use only local factory knowledge when sources are provided. Never claim to send images, production data or telemetry to cloud services. Keep it concise and operational.",
                (
                    "Component A17 photo: faint circular scoring and dull surface patch near the upper mounting point. Inspect it.",
                    "Bracket B04 image shows burrs around two bolt holes and a bright scratch on the machined face. What is the likely defect?",
                    "Housing C22 photo: coating looks uneven on the top edge, but dimensions are within tolerance. Is this a hold?",
                ),
                "Capture a component finding and classify whether production can continue.",
                "\uE722",
                "Issue: Possible inconsistent surface finish on A17 upper mount bracket.\nEvidence: The visible dull circular patch and scoring near the upper mounting point match the local A17 reference hold criteria.\nImmediate action: Hold this part and the previous ten parts from Tool Station 4.\nNext check: Run Tool Station 4 calibration and inspect the tool tip for wear, built-up edge or debris.\nLine status: Continue only after a clean calibration coupon and supervisor release.",
            ),
            BlueprintMode(
                "diagnose", "Diagnose", "Ask what caused the defect or fault", "Diagnose",
                "Likely cause",
                "You diagnose manufacturing defects from local maintenance manuals, fault-code references and machine telemetry. Return Issue, Evidence, Possible causes, Recommended checks and Escalate if needed. Be practical and safety-first: isolate equipment before inspection. Use only the provided local knowledge and the operator context.",
                (
                    "What type of defect is the inconsistent surface finish near the upper mounting point, and what could have caused it?",
                    "Fault QI-217 appeared twice after Tool Station 4 torque drifted above limit. What should I check first?",
                    "The last three parts from Cell 2 show chatter marks after the tool-change event. Diagnose the likely cause.",
                ),
                "Connect the defect with station telemetry, fault codes and likely root cause.",
                "\uE9D9",
                "Issue: Inconsistent surface finish near the upper mounting point.\nEvidence: QI-217 plus sample telemetry show torque drift +8%, spindle vibration 4.8 mm/s and tool life 91%.\nPossible causes: Tool Station 4 wear, calibration drift, debris on the mount, or a recent tool-change issue.\nRecommended checks:\n1. Isolate the cell.\n2. Inspect the tool tip for wear flats and built-up edge.\n3. Run the A17 calibration coupon.\n4. Review the last tool-change event.\nEscalate if drift remains outside +/-3%.",
            ),
            BlueprintMode(
                "assist", "Assist", "Ask for the maintenance procedure or next check", "Get procedure",
                "Procedure",
                "You guide factory operators through local maintenance and quality procedures. Give numbered steps, required safety checks, expected readings, pass/fail criteria and what to record in the quality log. Use only local manuals, SOPs, telemetry notes and fault-code references when they are provided.",
                (
                    "Show me the procedure for checking Tool Station 4 calibration.",
                    "What should I record before releasing the held A17 components?",
                    "Give me the offline troubleshooting steps for QI-217 when the network is disconnected.",
                ),
                "Retrieve the maintenance procedure and release checklist from local manuals.",
                "\uE90F",
                "Tool Station 4 calibration check:\n1. Press line pause and confirm the cell is isolated before opening the guard.\n2. Fit the certified A17 calibration coupon.\n3. Run the dry calibration cycle.\n4. Verify spindle vibration is below 3.5 mm/s.\n5. Compare commanded and measured torque; allowed drift is +/-3%.\n6. Inspect the tool tip for wear flats, built-up edge, scoring or debris.\n7. If out of limit, replace the tool, clean the mount and rerun calibration.\n8. Record the result, operator ID, component batch and held serial numbers in the quality log.",
                top_passages=5,
                min_relative_score=0.15,
            ),
        ),
        knowledge_pack="manufacturing-quality",
        local_drivers=(
            {"id": "offline", "title": "Works without the internet", "detail": "Inspection, defect triage and maintenance procedure lookup keep working when the factory network is disconnected."},
            {"id": "privacy", "title": "Production data stays local", "detail": "Component photos, machine telemetry, fault codes and quality records stay on this workstation."},
            {"id": "speed", "title": "Low-latency operator support", "detail": "Operators get immediate checks at the line instead of waiting for a cloud round trip."},
        ),
        brand={
            "domain": "Manufacturing quality station",
            "markGlyph": "\uE9D9",
            "workflowLabel": "Inspection workflow",
            "heroMetric1": "4 reference components",
            "heroMetric2": "3 local manuals",
            "heroMetric3": "Offline line support",
        },
        prototype={
            "enablesImageInput": True,
            "demoImage": "Assets/demo/a17-surface-defect.svg",
            "demoImageLabel": "A17 upper mount bracket",
            "demoImagePrompt": "Component A17 photo: faint circular scoring and dull surface patch near the upper mounting point. Inspect it.",
            "uploadPromptTemplate": "Uploaded component image: {fileName}. Inspect the visible manufacturing defect and recommend next checks.",
        },
        workspace={
            "layout": "inspection-console",
            "title": "Inspection queue",
            "subtitle": "Open quality holds from Line 2",
            "items": [
                {
                    "title": "A17 surface anomaly",
                    "subtitle": "Batch 24-10-07 · Tool Station 4",
                    "status": "Hold",
                    "priority": "High",
                    "prompt": "Component A17 photo: faint circular scoring and dull surface patch near the upper mounting point. Inspect it.",
                    "context": {
                        "component": "A17 upper mount bracket, batch 24-10-07",
                        "station": "Cell 2 / Tool Station 4",
                        "telemetry": "QI-217, torque drift +8%, spindle vibration 4.8 mm/s, tool life 91%"
                    }
                },
                {
                    "title": "B04 bolt-hole burrs",
                    "subtitle": "Batch 24-10-07 · Deburr Cell 1",
                    "status": "Review",
                    "priority": "Medium",
                    "prompt": "Bracket B04 image shows burrs around two bolt holes and a bright scratch on the machined face. What is the likely defect?",
                    "context": {
                        "component": "B04 actuator bracket, batch 24-10-07",
                        "station": "Deburr Cell 1",
                        "telemetry": "QI-104, air blast pressure nominal, deburr wheel life 88%"
                    }
                },
                {
                    "title": "C22 coating variation",
                    "subtitle": "Batch 24-10-08 · Coating Line 3",
                    "status": "Triage",
                    "priority": "Low",
                    "prompt": "Housing C22 photo: coating looks uneven on the top edge, but dimensions are within tolerance. Is this a hold?",
                    "context": {
                        "component": "C22 control housing, batch 24-10-08",
                        "station": "Coating Line 3",
                        "telemetry": "No fault code, coating thickness 62-71 microns, dimensions in tolerance"
                    }
                }
            ]
        },
    ),
    Blueprint(
        id="patient-companion",
        industry="healthcare",
        cues=("patient", "companion", "discharge", "care plan", "clinician-approved", "medication",
              "symptom", "appointment", "recovery", "aftercare", "offline"),
        title="Patient Companion",
        tagline="Answer patient questions from clinician-approved aftercare guidance on the device.",
        organisation="Zava Health",
        context_title="Patient context",
        context_fields=(
            {"key": "procedure", "label": "Procedure", "placeholder": "e.g. knee arthroscopy discharge"},
            {"key": "medication", "label": "Medication", "placeholder": "e.g. paracetamol, ibuprofen avoided"},
            {"key": "needs", "label": "Access needs", "placeholder": "e.g. large text, simple steps"},
        ),
        modes=(
            BlueprintMode(
                "aftercare", "Aftercare Q&A", "What does the patient want to understand?", "Answer",
                "Patient answer",
                "You explain clinician-approved aftercare guidance from {org}. You never diagnose, prescribe, or replace a clinician. Use simple language, quote only the local guidance, and clearly flag red symptoms that need urgent clinical review.",
                (
                    "I had knee arthroscopy yesterday. When can I remove the dressing and what should make me call the clinic?",
                    "Can I take ibuprofen if my discharge note says paracetamol first?",
                    "Explain my physio exercises in simple steps I can follow at home.",
                ),
                "Answer patient questions from approved discharge and aftercare guidance.",
                "\uE95E",
                scopes=("public", "staff"),
                top_passages=5,
            ),
            BlueprintMode(
                "prepare", "Visit prep", "What should we prepare for the next appointment?", "Prepare",
                "Visit prep",
                "You help patients prepare for follow-up appointments using only local care-plan guidance. Produce short checklists and questions to ask the clinician.",
                (
                    "Prepare questions for my 2-week follow-up after knee arthroscopy.",
                    "What should I track before my wound check appointment?",
                    "Create a plain-English checklist for tomorrow's physiotherapy visit.",
                ),
                "Turn care-plan guidance into simple follow-up checklists.",
                "\uE8FD",
                scopes=("public", "staff"),
            ),
        ),
        knowledge_pack="patient-companion",
        local_drivers=(
            {"id": "privacy", "title": "Health information stays local", "detail": "Patient notes and questions stay on the device instead of being sent to a cloud AI service."},
            {"id": "offline", "title": "Useful at home or bedside", "detail": "Approved guidance remains available when Wi-Fi is poor or unavailable."},
            {"id": "compliance", "title": "Clinician-approved grounding", "detail": "Answers are constrained to the local care materials supplied by the clinical team."},
        ),
        brand={
            "domain": "Patient aftercare companion",
            "markGlyph": "\uE95E",
            "workflowLabel": "Care journey",
            "heroMetric1": "Clinician-approved",
            "heroMetric2": "Red-flag guidance",
            "heroMetric3": "Offline at bedside",
        },
    ),
    Blueprint(
        id="contract-review",
        industry="legal",
        cues=("contract", "clause", "nda", "liability", "redline", "agreement", "supplier",
              "terms", "legal", "lawyer", "risk"),
        title="Contract Risk Reviewer",
        tagline="Review clauses against a local playbook and draft negotiation notes without exposing client documents.",
        organisation="Zava Legal",
        context_title="Matter context",
        context_fields=(
            {"key": "client", "label": "Client", "placeholder": "e.g. Northwind Ltd"},
            {"key": "position", "label": "Preferred position", "placeholder": "e.g. customer-friendly"},
            {"key": "jurisdiction", "label": "Jurisdiction", "placeholder": "e.g. England and Wales"},
        ),
        modes=(
            BlueprintMode(
                "review", "Clause review", "Paste the clause or issue to review", "Review",
                "Review notes",
                "You assist {org} lawyers. Review only against the local clause playbook. Return Summary, Risk, Fallback position, Suggested drafting and Questions for counsel. Do not present output as legal advice.",
                (
                    "Review this liability cap: Supplier's total liability shall not exceed fees paid in the 3 months before the claim.",
                    "Check this auto-renewal clause for customer risk and propose a fallback.",
                    "Summarise risks in a clause that excludes all indirect losses and all service credits.",
                ),
                "Review a clause against the local risk playbook.",
                "\uE8A5",
                scopes=("public", "staff"),
            ),
            BlueprintMode(
                "negotiate", "Negotiation prep", "What position are we taking?", "Prepare",
                "Negotiation plan",
                "You prepare negotiation notes from the local playbook. Keep the output practical: asks, gives, red lines, and escalation points.",
                (
                    "Prepare negotiation notes for a supplier refusing audit rights.",
                    "Create a fallback ladder for data-processing liability.",
                    "Draft a polite email requesting a 30-day termination right.",
                ),
                "Create a matter-ready fallback ladder and negotiation plan.",
                "\uE8D7",
                scopes=("public", "staff"),
            ),
        ),
        knowledge_pack="contract-review",
        local_drivers=(
            {"id": "privacy", "title": "Client documents stay local", "detail": "Contracts, matter notes and negotiation positions remain on the device."},
            {"id": "compliance", "title": "Grounded in the playbook", "detail": "Output is constrained to approved local clause guidance and review standards."},
            {"id": "offline", "title": "Works during travel", "detail": "Lawyers can review and prepare without reliable connectivity."},
        ),
        brand={
            "domain": "Legal matter workbench",
            "markGlyph": "\uE8A5",
            "workflowLabel": "Matter workflow",
            "heroMetric1": "Clause playbook",
            "heroMetric2": "Risk positions",
            "heroMetric3": "Private by design",
        },
    ),
)
