"""
Dynamic product designer for Zava Live Build.

This deliberately avoids fixed industry profiles and use-case blueprints. It
turns a vague idea into a concrete product concept: domain, work records,
workflow steps, local data, demo responses and a layout archetype.
"""

from __future__ import annotations

import re


def design_app(idea: str, name: str | None, analysis) -> dict:
    text = idea.lower()
    domain = _domain(text)
    title = name or _title(idea, domain)
    visual = any(w in text for w in ("image", "photo", "camera", "scan", "inspect", "visual", "defect"))
    layout = _layout(domain, visual, text)
    records = _records(domain, text)
    modes = _modes(domain, visual, records)
    local_drivers = _local_drivers(domain, text)
    return {
        "title": title,
        "tagline": _tagline(domain, title),
        "system_prompt": modes[0]["systemPrompt"],
        "input_label": modes[0]["inputLabel"],
        "sample_prompts": modes[0]["samplePrompts"],
        "industry": domain["id"],
        "industry_name": domain["name"],
        "organisation": domain["org"],
        "theme": _theme(domain),
        "modes": modes,
        "context_title": domain["contextTitle"],
        "context_fields": domain["contextFields"],
        "local_drivers": local_drivers,
        "knowledge": True,
        "knowledge_pack": None,
        "languages": [],
        "brand": {
            "domain": domain["workspace"],
            "markGlyph": domain["glyph"],
            "workflowLabel": domain["workflow"],
            "heroMetric1": records[0]["status"],
            "heroMetric2": f"{len(records)} live records",
            "heroMetric3": "Offline-ready",
        },
        "prototype": {
            "enablesImageInput": visual,
            "demoImage": "Assets/demo/demo-evidence.svg" if visual else "",
            "demoImageLabel": records[0]["title"],
            "demoImagePrompt": records[0]["prompt"],
            "uploadPromptTemplate": f"Uploaded file {{fileName}}. {_upload_instruction(domain)}",
        },
        "workspace": {
            "layout": layout,
            "title": domain["queueTitle"],
            "subtitle": domain["queueSubtitle"],
            "items": records,
        },
    }


def _domain(text: str) -> dict:
    domains = [
        ("manufacturing", ("factory", "manufactur", "quality", "defect", "component", "production", "tool", "station"),
         "Zava Manufacturing", "Quality operations", "Inspection queue", "Open quality holds", "Inspection workflow", "\uE9D9"),
        ("healthcare", ("patient", "clinic", "hospital", "care", "discharge", "medication", "appointment"),
         "Zava Health", "Care operations", "Patient worklist", "Patients needing support", "Care journey", "\uE95E"),
        ("legal", ("contract", "clause", "legal", "lawyer", "nda", "liability", "agreement"),
         "Zava Legal", "Matter operations", "Matter queue", "Documents needing review", "Matter workflow", "\uE8A5"),
        ("hospitality", ("hotel", "guest", "concierge", "room", "reception", "itinerary"),
         "Zava Harbour Hotel", "Guest operations", "Guest queue", "Guest requests in progress", "Guest workflow", "\uE707"),
        ("retail", ("retail", "store", "customer", "inventory", "return", "product"),
         "Zava Stores", "Store operations", "Store queue", "Customer and stock tasks", "Store workflow", "\uE719"),
        ("finance", ("finance", "bank", "loan", "claim", "portfolio", "mortgage", "wealth"),
         "Zava Financial", "Advisor operations", "Client queue", "Client work in progress", "Client workflow", "\uE8C7"),
    ]
    best = max(domains, key=lambda d: sum(1 for cue in d[1] if cue in text))
    if sum(1 for cue in best[1] if cue in text) == 0:
        best = ("operations", (), "Zava", "Operations workspace", "Work queue", "Active work items", "Workflow", "\uE946")
    id_, _, org, workspace, queue, subtitle, workflow, glyph = best
    return {
        "id": id_, "name": id_.replace("-", " ").title(), "org": org, "workspace": workspace,
        "queueTitle": queue, "queueSubtitle": subtitle, "workflow": workflow, "glyph": glyph,
        "contextTitle": _context_title(id_),
        "contextFields": _context_fields(id_),
    }


def _context_title(domain: str) -> str:
    return {
        "manufacturing": "Production context",
        "healthcare": "Patient context",
        "legal": "Matter context",
        "hospitality": "Guest context",
        "retail": "Customer context",
        "finance": "Client context",
    }.get(domain, "Work context")


def _context_fields(domain: str) -> list[dict[str, str]]:
    fields = {
        "manufacturing": [("asset", "Component", "e.g. A17 upper mount bracket"), ("location", "Station", "e.g. Tool Station 4"), ("signal", "Telemetry", "e.g. QI-217, torque drift +8%")],
        "healthcare": [("asset", "Patient journey", "e.g. knee arthroscopy discharge"), ("location", "Care setting", "e.g. home recovery"), ("signal", "Concern", "e.g. dressing, pain, medication")],
        "legal": [("asset", "Document", "e.g. supplier agreement"), ("location", "Matter", "e.g. Northwind renewal"), ("signal", "Position", "e.g. customer-friendly")],
        "hospitality": [("asset", "Guest", "e.g. family of four"), ("location", "Stay", "e.g. room 214, checkout 11am"), ("signal", "Need", "e.g. rainy-day plan")],
        "retail": [("asset", "Customer", "e.g. size M, budget £120"), ("location", "Store area", "e.g. outerwear"), ("signal", "Need", "e.g. waterproof jacket")],
        "finance": [("asset", "Client", "e.g. cautious investor"), ("location", "Product", "e.g. mortgage"), ("signal", "Goal", "e.g. retire at 60")],
    }.get(domain, [("asset", "Record", "e.g. active case"), ("location", "Area", "e.g. team or site"), ("signal", "Signal", "e.g. issue or request")])
    return [{"key": k, "label": label, "placeholder": placeholder} for k, label, placeholder in fields]


def _layout(domain: dict, visual: bool, text: str) -> str:
    if visual:
        return "visual-workbench"
    if domain["id"] in ("legal", "finance"):
        return "review-workbench"
    if domain["id"] in ("healthcare", "hospitality"):
        return "journey-workbench"
    return "operations-workbench"


def _title(idea: str, domain: dict) -> str:
    m = re.search(r"\b([A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+){0,3})\s*:", idea)
    if m:
        return m.group(1)
    suffix = {
        "manufacturing": "Quality Console",
        "healthcare": "Care Companion",
        "legal": "Matter Review",
        "hospitality": "Guest Desk",
        "retail": "Store Advisor",
        "finance": "Client Advisor",
    }.get(domain["id"], "Operations Console")
    return f"{domain['org'].split()[-1]} {suffix}" if domain["id"] != "operations" else "Zava Operations Console"


def _tagline(domain: dict, title: str) -> str:
    return f"Resolve real {domain['workspace'].lower()} work from local records, evidence and procedures."


def _theme(domain: dict) -> dict:
    accents = {
        "manufacturing": ("#C98A00", "#FFF3D6", "#F4F5F3", "#16191C", "Bahnschrift"),
        "healthcare": ("#0B6E8A", "#E0F0F5", "#F3F7FA", "#13232B", "Segoe UI Variable Display"),
        "legal": ("#6B1F2A", "#F3E4E6", "#F8F6F3", "#1F1A1A", "Georgia"),
        "hospitality": ("#1F5F5B", "#E2EFEC", "#F7F3EC", "#1E2A2B", "Georgia"),
        "retail": ("#B4441C", "#FBE8DE", "#FAF7F4", "#231C18", "Segoe UI Variable Display"),
        "finance": ("#1D3A6E", "#E3E9F4", "#F5F7FA", "#121A2A", "Segoe UI Variable Display"),
    }.get(domain["id"], ("#2F5BD3", "#E5ECFB", "#F6F7F9", "#171B22", "Segoe UI Variable Display"))
    accent, soft, canvas, ink, font = accents
    return {"mode": "light", "canvas": canvas, "surface": "#FFFFFF", "surface2": "#EEF0F4", "stroke": "#DFE3EA",
            "ink": ink, "ink2": "#4A5160", "muted": "#8A8F98", "accent": accent, "accentInk": "#FFFFFF",
            "accentSoft": soft, "displayFont": font, "corner": 14}


def _records(domain: dict, text: str) -> list[dict]:
    if domain["id"] == "manufacturing":
        return [
            _record("A17 surface anomaly", "Batch 24-10-07 · Tool Station 4", "Hold", "High",
                    "Component A17 photo: faint circular scoring and dull surface patch near the upper mounting point. Inspect it.",
                    "A17 upper mount bracket", "Cell 2 / Tool Station 4", "QI-217, torque drift +8%, spindle vibration 4.8 mm/s, tool life 91%"),
            _record("B04 bolt-hole burrs", "Batch 24-10-07 · Deburr Cell 1", "Review", "Medium",
                    "Bracket B04 image shows burrs around two bolt holes and a bright scratch on the machined face. What is the likely defect?",
                    "B04 actuator bracket", "Deburr Cell 1", "QI-104, deburr wheel life 88%, air blast nominal"),
            _record("C22 coating variation", "Batch 24-10-08 · Coating Line 3", "Triage", "Low",
                    "Housing C22 photo: coating looks uneven on the top edge, but dimensions are within tolerance. Is this a hold?",
                    "C22 control housing", "Coating Line 3", "Thickness 62-71 microns, dimensions in tolerance"),
        ]
    if domain["id"] == "healthcare":
        return [
            _record("Post-op dressing question", "Knee arthroscopy · Day 1", "Needs answer", "High", "When can I remove the dressing and what should make me call the clinic?", "Knee arthroscopy discharge", "Home recovery", "Pain 4/10, dressing dry"),
            _record("Medication clarification", "Discharge note · Analgesia", "Review", "Medium", "Can I take ibuprofen if my discharge note says paracetamol first?", "Discharge medication", "Home recovery", "NSAID caution flag"),
            _record("Follow-up prep", "2-week appointment", "Prepare", "Low", "Prepare questions for my 2-week follow-up.", "Follow-up appointment", "Clinic", "Mobility improving"),
        ]
    return [
        _record("Priority record", "Imported from local workspace", "Open", "High", "Summarise the issue and recommend the next action.", "Active record", "Local workspace", "Private context available"),
        _record("Procedure request", "Reference material available", "Open", "Medium", "Show the relevant procedure and checklist.", "Procedure", "Local workspace", "Knowledge pack available"),
        _record("Customer-ready summary", "Draft needed", "Open", "Low", "Create a concise summary for the stakeholder.", "Summary", "Local workspace", "Output required"),
    ]


def _record(title: str, subtitle: str, status: str, priority: str, prompt: str, asset: str, location: str, signal: str) -> dict:
    return {"title": title, "subtitle": subtitle, "status": status, "priority": priority, "prompt": prompt,
            "context": {"asset": asset, "location": location, "signal": signal}}


def _modes(domain: dict, visual: bool, records: list[dict]) -> list[dict]:
    labels = _mode_labels(domain["id"], visual)
    modes = []
    for label in labels:
        modes.append({
            "id": label[0].lower().replace(" ", "-"), "name": label[0], "inputLabel": label[1], "action": label[2],
            "outputTitle": label[3], "systemPrompt": f"You are the {domain['workspace']} specialist for {domain['org']}. Use local records, evidence and reference material only. Give practical next actions.",
            "samplePrompts": [records[0]["prompt"], records[1]["prompt"], records[2]["prompt"]],
            "scopes": ["public", "staff"], "usesContext": True, "description": label[4], "icon": label[5],
            "demoResponse": _demo_response(domain, label[0], records[0]), "usesLanguage": False,
            "defaultLanguage": "English", "topPassages": 4, "diversify": True, "minRelativeScore": 0.2,
        })
    return modes


def _mode_labels(domain: str, visual: bool) -> list[tuple[str, str, str, str, str, str]]:
    if domain == "manufacturing" or visual:
        return [
            ("Inspect", "Selected evidence or uploaded image", "Run inspection", "Inspection decision", "Classify the selected record and decide whether to hold or release.", "\uE722"),
            ("Diagnose", "Defect, telemetry or fault code", "Diagnose", "Likely cause", "Connect the finding to local telemetry and likely root cause.", "\uE9D9"),
            ("Resolve", "Procedure or release question", "Get procedure", "Procedure", "Show the local checklist and release criteria.", "\uE90F"),
        ]
    return [
        ("Triage", "Selected record or request", "Triage", "Triage decision", "Understand the active record and what needs attention.", "\uE9D9"),
        ("Advise", "Question or next step", "Advise", "Recommendation", "Use local context and references to recommend next action.", "\uE8A5"),
        ("Summarise", "Stakeholder summary request", "Summarise", "Summary", "Create a concise, ready-to-share summary.", "\uE8D7"),
    ]


def _demo_response(domain: dict, mode: str, record: dict) -> str:
    if domain["id"] == "manufacturing":
        if mode == "Inspect":
            return "Decision: HOLD\nFinding: Possible inconsistent surface finish near the upper mounting point.\nEvidence: The A17 reference allows uniform satin finish only; the selected evidence shows circular scoring and a dull patch.\nImmediate action: Quarantine this part and the previous ten parts from Tool Station 4.\nNext step: Run the Tool Station 4 calibration check before release."
        if mode == "Diagnose":
            return "Likely cause: Tool Station 4 calibration drift or tool wear.\nEvidence: QI-217, torque drift +8%, spindle vibration 4.8 mm/s and tool life 91%.\nChecks:\n1. Isolate Cell 2.\n2. Inspect tool tip for wear flats or built-up edge.\n3. Run the A17 calibration coupon.\n4. Review the last tool-change event."
        return "Procedure: Tool Station 4 calibration check\n1. Pause the line and isolate the cell.\n2. Fit the certified A17 calibration coupon.\n3. Run the dry calibration cycle.\n4. Confirm vibration below 3.5 mm/s and torque drift within +/-3%.\n5. Replace the tool and rerun if out of limit.\n6. Record result, operator ID, batch and held serial numbers."
    return f"Decision for {record['title']}: proceed with the recommended local workflow.\nEvidence: {record['context']['signal']}.\nNext actions:\n1. Review the selected record.\n2. Check the local reference material.\n3. Capture the outcome in the work queue.\n4. Escalate only if the local guidance requires it."


def _local_drivers(domain: dict, text: str) -> list[dict[str, str]]:
    return [
        {"id": "privacy", "title": "Data stays local", "detail": "Records, evidence and prompts stay on this device."},
        {"id": "offline", "title": "Works offline", "detail": "Core workflow and references remain available without network connectivity."},
        {"id": "speed", "title": "Immediate decisions", "detail": "The app supports the user at the point of work without a cloud round trip."},
    ]


def _upload_instruction(domain: dict) -> str:
    if domain["id"] == "manufacturing":
        return "Inspect the visible manufacturing defect and recommend next checks."
    return "Use the uploaded file as local evidence and recommend the next action."
