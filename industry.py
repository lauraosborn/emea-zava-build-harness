"""
Zava Live Build -- industry design profiles.

The generated app must look and read like a product for the idea's industry, not like
a model benchmark. Each profile sets:

  * theme: palette (light or dark), heading font, corner radius
  * wording: the main action, the output title, context panel title
  * context fields: private details that shape answers and never leave the device
  * modes: the jobs the app does (for example guest concierge + staff assistant)
  * sample prompts: realistic requests with inline data, per mode
  * private data phrase: used to explain *why* this app runs locally

Local AI drivers (privacy, offline, speed, cost, compliance) are detected from the
idea and become the app's "Why this runs on this device" story. Model, device and
throughput details are deliberately secondary: they live in a footer ribbon.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


@dataclass
class Theme:
    mode: str              # "light" | "dark"
    canvas: str
    surface: str
    surface2: str
    stroke: str
    ink: str
    ink2: str
    muted: str
    accent: str
    accent_ink: str        # text drawn on the accent colour
    accent_soft: str       # tinted background for chips and highlights
    display_font: str
    corner: int = 14


@dataclass
class ContextField:
    key: str
    label: str
    placeholder: str


@dataclass
class ModeSpec:
    id: str
    name: str
    input_label: str
    action: str
    output_title: str
    system_core: str
    samples: list[str]
    scopes: list[str] = field(default_factory=lambda: ["public"])
    uses_context: bool = False
    uses_language: bool = False
    top_passages: int = 4
    diversify: bool = False
    min_relative_score: float = 0.25


@dataclass
class IndustryProfile:
    id: str
    name: str
    cues: tuple[str, ...]
    theme: Theme
    private_data: str                  # "guest details, allergies and room numbers"
    audience: str                      # "guests"
    context_title: str = ""
    context_fields: list[ContextField] = field(default_factory=list)
    primary: ModeSpec | None = None
    staff: ModeSpec | None = None
    staff_cues: tuple[str, ...] = ("staff", "employee", "reception", "colleague", "team member", "agent")
    knowledge_pack: str | None = None
    default_drivers: tuple[str, ...] = ("privacy", "offline", "speed")


def _light(accent, accent_soft, canvas="#F7F7F5", surface="#FFFFFF", surface2="#F1F1EE",
           stroke="#E3E3DE", ink="#1C1F23", ink2="#4A4F57", muted="#8A8F98",
           accent_ink="#FFFFFF", font="Segoe UI Variable Display", corner=14) -> Theme:
    return Theme("light", canvas, surface, surface2, stroke, ink, ink2, muted,
                 accent, accent_ink, accent_soft, font, corner)


TRANSLATE_CORE = ("You are a multilingual assistant for {org}. Reply ONLY in {language}, written directly to "
                  "the reader. If asked to translate, translate faithfully and add nothing. Otherwise summarise "
                  "only the knowledge provided. Keep times, prices and names exactly as given.")

PROFILES: list[IndustryProfile] = [
    IndustryProfile(
        id="hospitality", name="Hospitality",
        cues=("hotel", "guest", "concierge", "reception", "resort", "check-in", "checkout", "itinerar",
              "restaurant", "spa", "front desk", "housekeeping", "hospitality", "tourism", "visitor"),
        theme=_light("#1F5F5B", "#E2EFEC", canvas="#F7F3EC", surface="#FFFFFF", surface2="#F2ECE2",
                     stroke="#E4DACB", ink="#1E2A2B", ink2="#4B5657", muted="#8C8F86", font="Georgia", corner=16),
        private_data="guest details, allergies and room numbers", audience="guests",
        context_title="Guest profile",
        context_fields=[ContextField("party", "Party", "e.g. 2 adults, 2 children (7, 10)"),
                        ContextField("dietary", "Allergies and dietary needs", "e.g. nut allergy (daughter)"),
                        ContextField("checkout", "Checkout", "e.g. tomorrow 11:00")],
        primary=ModeSpec(
            "guest", "Guest concierge", "How can we help with your stay?", "Ask the concierge", "Your plan",
            "You are the concierge at {org}. Use only places in the knowledge. Plan only what the guest asked "
            "for: something to do, then a meal. If the guest mentions lunch or dinner at all (even 'before "
            "going for lunch'), include a place to eat for it. At most 3 stops, each place once. "
            "Morning and Afternoon stops come from 'Things to do' or hotel facilities; Lunch and Dinner stops "
            "come from 'Places to eat' or the hotel restaurant, and must be open at that time. If an allergy is "
            "mentioned, pick only venues the knowledge calls safe for it (for nuts: \"nut-free\") and name one "
            "to avoid; otherwise do not mention allergies. When the guest asks for lunch, label that stop Lunch; when they ask for dinner, label it Dinner. "
            "Answer in this format: one line per stop, then a single Tip line at the very end, and nothing else "
            "(no directions section, no repeated tips):\n"
            "<Morning or Afternoon>: <place to visit> (<its opening hours from the knowledge>) - <walking time>. <why it suits them>\n"
            "<Lunch or Dinner, whichever meal the guest asked for>: <place to eat> (<its opening hours from the knowledge>) - <walking time>. <why it suits them>\n"
            "Add a second activity only if the guest wants a full day. Never add a meal the guest did not mention.\n"
            "Tip: <one short sentence from the knowledge>\n"
            "Choose each label from the time of day the guest asked about and the place's opening hours. "
            "Reply in the language the guest used.",
            ["We're a family of four and it's raining. Suggest a rainy-day itinerary nearby, somewhere suitable for dinner, and explain how we get there.",
             "My daughter has a nut allergy. We're leaving tomorrow at 11am and we'd like something to do this morning before going for lunch.",
             "What time is breakfast at the weekend, and can we leave our bags here after checkout?"],
            scopes=["public"], uses_context=True, top_passages=6, diversify=True),
        staff=ModeSpec(
            "staff", "Staff assistant", "What do you need help with?", "Get the steps", "What to do",
            "You are the staff assistant for {org} reception and duty teams. Answer ONLY from the procedure "
            "provided: copy its steps in order and never add steps, people, policies or phone extensions it "
            "does not contain. Answer exactly in this format:\n"
            "**Steps**\n1. <step from the procedure>\n2. <step from the procedure>\n"
            "**Escalate:** <when, who and extension, copied from the procedure>\n"
            "Include the Escalate line only when the procedure itself names an engineer, a manager or an "
            "extension to contact; otherwise stop after the steps.",
            ["A guest in room 214 says the air conditioning isn't working. What should I check before escalating it?",
             "A guest's key card has stopped working and they're getting frustrated. Walk me through it.",
             "Someone on the phone says they're a colleague of one of our guests and asks which room she's in. What do I say?"],
            scopes=["staff", "public"], top_passages=3, min_relative_score=0.55),
        knowledge_pack="hospitality"),
    IndustryProfile(
        id="healthcare", name="Healthcare",
        cues=("patient", "clinic", "clinical", "doctor", "nurse", "gp ", "hospital", "health", "medical",
              "triage", "symptom", "care home", "pharmac", "ward", "discharge"),
        theme=_light("#0B6E8A", "#E0F0F5", canvas="#F3F7FA", surface2="#EBF2F6", stroke="#D9E4EA",
                     ink="#13232B", ink2="#46575F"),
        private_data="patient notes, conditions and medication", audience="patients",
        context_title="Patient context",
        context_fields=[ContextField("age", "Age and sex", "e.g. 67, female"),
                        ContextField("conditions", "Conditions and allergies", "e.g. type 2 diabetes, penicillin allergy"),
                        ContextField("medication", "Current medication", "e.g. metformin 500mg twice daily")],
        primary=ModeSpec(
            "clinician", "Clinician assistant", "What would you like to prepare?", "Prepare", "Draft",
            "You assist clinicians at {org}. You draft, summarise and organise; you never diagnose or "
            "prescribe. Flag anything that needs clinical review. Use plain, precise language and short sections.",
            ["Summarise for handover: 67F, admitted with chest pain, troponin negative x2, ECG normal, pain settled, plan discharge with GP follow-up in 1 week.",
             "Turn these notes into a plain-English discharge letter for the patient: knee arthroscopy, keep dressing dry 48h, paracetamol for pain, return if fever or calf swelling.",
             "List the questions I should ask a patient reporting dizziness after starting amlodipine 5mg last week."],
            uses_context=True),
        staff=ModeSpec(
            "admin", "Front desk", "What do you need help with?", "Get the steps", "What to do",
            "You support {org} reception staff with procedures. Follow only the procedures provided. Never "
            "share clinical details. Give numbered steps and when to escalate.",
            ["A patient is upset that their appointment was cancelled. What do I do?",
             "Someone calls asking whether their father is a patient here. What can I say?",
             "How do I register a new patient who has no NHS number?"],
            scopes=["staff", "public"], top_passages=3, min_relative_score=0.55),
        default_drivers=("privacy", "compliance", "offline")),
    IndustryProfile(
        id="retail", name="Retail",
        cues=("retail", "store", "shop", "shopper", "product", "inventory", "stock", "merchandis", "basket",
              "checkout till", "ecommerce", "e-commerce", "customer service", "returns"),
        theme=_light("#B4441C", "#FBE8DE", canvas="#FAF7F4", surface2="#F4EEE8", stroke="#E9DFD5",
                     ink="#231C18", ink2="#5A4F49"),
        private_data="customer details, preferences and purchase history", audience="customers",
        context_title="Customer",
        context_fields=[ContextField("preferences", "Preferences", "e.g. vegan, prefers neutral colours"),
                        ContextField("budget", "Budget", "e.g. under £80"),
                        ContextField("size", "Sizes", "e.g. M, shoe 6")],
        primary=ModeSpec(
            "advisor", "Shopping advisor", "What is the customer looking for?", "Find options", "Recommendations",
            "You are a store advisor at {org}. Recommend only products in the knowledge provided, within budget "
            "and preferences, with price and where to find them. Three options at most, with one line on why.",
            ["A customer wants a waterproof jacket for walking holidays, budget £120, size M. What should I show them?",
             "Suggest a gift for a 10-year-old who loves science, under £30.",
             "A customer wants to return shoes bought 40 days ago without a receipt. What are the options?"],
            uses_context=True),
        staff=ModeSpec(
            "colleague", "Colleague help", "What do you need help with?", "Get the steps", "What to do",
            "You support {org} store colleagues with procedures. Follow only the procedures provided. Give "
            "numbered steps and when to call a manager.",
            ["The card terminal on till 3 keeps declining contactless. What should I check?",
             "A delivery has arrived with two cartons damaged. What do I do?",
             "How do I process a price match request?"],
            scopes=["staff", "public"], top_passages=3, min_relative_score=0.55),
        default_drivers=("privacy", "speed", "cost")),
    IndustryProfile(
        id="field", name="Field service and manufacturing",
        cues=("field", "engineer", "technician", "site", "maintenance", "repair", "equipment", "asset",
              "factory", "manufactur", "plant", "work order", "inspection", "quality", "defect",
              "calibration", "fault", "machine", "operator", "workstation", "utility"),
        theme=_light("#C98A00", "#FFF3D6", canvas="#F4F5F3", surface2="#ECEEEA", stroke="#DADDD6",
                     ink="#16191C", ink2="#454B52", accent_ink="#16191C", font="Bahnschrift", corner=10),
        private_data="site, asset and customer records", audience="engineers",
        context_title="Job",
        context_fields=[ContextField("site", "Site and asset", "e.g. Plant 2, conveyor C4"),
                        ContextField("model", "Equipment model", "e.g. Zava DriveLine 300"),
                        ContextField("fault", "Fault code", "e.g. E-217")],
        primary=ModeSpec(
            "engineer", "Engineer assistant", "What's happening on site?", "Diagnose", "Next steps",
            "You assist {org} field engineers. Be practical and safety-first: isolate before inspecting. Give "
            "likely causes, then numbered checks in order, then when to escalate.",
            ["Conveyor C4 stops every 40 minutes and restarts after 2. Fault code E-217 on the drive. What are the likely causes and first three checks?",
             "Turn these notes into a work order: replaced belt tensioner, 1.5h labour, belt worn on left edge, recommend alignment check next visit.",
             "Write a short, plain customer summary of today's visit: pump seal replaced, leak stopped, pressure normal, next service due in March."],
            uses_context=True),
        default_drivers=("offline", "privacy", "speed")),
    IndustryProfile(
        id="finance", name="Financial services",
        cues=("bank", "finance", "financial", "loan", "mortgage", "invest", "wealth", "insurance", "claim",
              "underwrit", "advisor", "adviser", "portfolio", "kyc", "fraud", "account"),
        theme=_light("#1D3A6E", "#E3E9F4", canvas="#F5F7FA", surface2="#EDF1F6", stroke="#DCE2EB",
                     ink="#121A2A", ink2="#465066"),
        private_data="client finances and identity documents", audience="clients",
        context_title="Client",
        context_fields=[ContextField("profile", "Risk profile", "e.g. cautious"),
                        ContextField("goals", "Goals", "e.g. retire at 60, help with house deposit"),
                        ContextField("holdings", "Holdings", "e.g. ISA £40k, pension £210k")],
        primary=ModeSpec(
            "advisor", "Adviser assistant", "What are you preparing?", "Prepare", "Draft",
            "You assist {org} advisers. Draft and summarise only; never give regulated advice as fact. Flag "
            "assumptions and anything needing compliance review. Be concise.",
            ["Summarise this client meeting: wants to retire at 60, cautious, ISA £40k, pension £210k, worried about inflation, daughter buying a flat in 2 years.",
             "Draft a plain-English explanation of the difference between a fixed and tracker mortgage for a first-time buyer.",
             "List the documents I need to request for a small business loan application."],
            uses_context=True),
        default_drivers=("privacy", "compliance", "cost")),
    IndustryProfile(
        id="legal", name="Legal",
        cues=("legal", "law", "lawyer", "solicitor", "contract", "clause", "litigation", "case file",
              "counsel", "paralegal", "compliance", "nda"),
        theme=_light("#6B1F2A", "#F3E4E6", canvas="#F8F6F3", surface2="#F1EDE8", stroke="#E4DDD5",
                     ink="#1F1A1A", ink2="#524948", font="Georgia", corner=10),
        private_data="client matters and privileged documents", audience="clients",
        context_title="Matter",
        context_fields=[ContextField("client", "Client", "e.g. Northwind Ltd"),
                        ContextField("jurisdiction", "Jurisdiction", "e.g. England and Wales"),
                        ContextField("type", "Matter type", "e.g. supplier agreement")],
        primary=ModeSpec(
            "counsel", "Matter assistant", "What do you need to review?", "Review", "Review notes",
            "You assist {org} lawyers. Summarise and flag; never present output as legal advice. Quote the "
            "clause you rely on. Use short headed sections: Summary, Risks, Suggested changes.",
            ["Review this clause: 'Supplier's total liability shall not exceed the fees paid in the 3 months before the claim.' What are the risks for the customer?",
             "Summarise the key dates and obligations: 12-month term from 1 Jan, 90 days' notice to terminate, price review each April, payment within 30 days.",
             "Draft a polite email asking the other side to extend the response deadline by 7 days."],
            uses_context=True),
        default_drivers=("privacy", "compliance", "offline")),
    IndustryProfile(
        id="education", name="Education",
        cues=("school", "student", "pupil", "teacher", "lesson", "classroom", "tutor", "learner",
              "university", "course", "homework", "curriculum", "education"),
        theme=_light("#4B3FBF", "#EAE8FB", canvas="#F7F7FC", surface2="#EFEFF8", stroke="#E0DFEE",
                     ink="#1B1A2E", ink2="#4D4B66"),
        private_data="student records and assessments", audience="students",
        context_title="Learner",
        context_fields=[ContextField("year", "Year group", "e.g. Year 8"),
                        ContextField("subject", "Subject", "e.g. Science"),
                        ContextField("needs", "Support needs", "e.g. dyslexia, prefers visuals")],
        primary=ModeSpec(
            "teacher", "Teaching assistant", "What are you planning?", "Create", "Ready to use",
            "You help {org} teachers. Produce classroom-ready material pitched at the learner's level, with "
            "clear headings. Adapt for any support needs.",
            ["Create a 20-minute starter activity on photosynthesis for Year 8, with three quick questions.",
             "Give feedback on this paragraph and one next step: 'The Romans built roads becuase they wanted to move armys fast.'",
             "Explain fractions to a 9-year-old using pizza, in five short sentences."],
            uses_context=True),
        default_drivers=("privacy", "cost", "offline")),
    IndustryProfile(
        id="public", name="Public sector",
        cues=("council", "citizen", "resident", "public sector", "government", "benefit", "housing",
              "local authority", "library", "social care", "permit"),
        theme=_light("#0A5C8A", "#E1EEF6", canvas="#F5F7F8", surface2="#ECF0F2", stroke="#DCE3E7",
                     ink="#14212B", ink2="#47545D"),
        private_data="resident records and circumstances", audience="residents",
        context_title="Resident",
        context_fields=[ContextField("area", "Area", "e.g. North ward"),
                        ContextField("circumstances", "Circumstances", "e.g. single parent, works part time"),
                        ContextField("language", "Preferred language", "e.g. Polish")],
        primary=ModeSpec(
            "advisor", "Resident adviser", "How can we help?", "Find help", "What you can do",
            "You help {org} staff support residents. Use plain English, list options and the next step, and "
            "note any deadline.",
            ["A resident's boiler has broken and they have a newborn. What support can we offer today?",
             "Explain how to apply for a parking permit in five simple steps.",
             "Summarise this complaint and suggest a response: bins missed three weeks running on Elm Road."],
            uses_context=True),
        default_drivers=("privacy", "compliance", "cost")),
    IndustryProfile(
        id="software", name="Software development",
        cues=("code", "coding", "developer", "script", "powershell", "python", "sql", "regex", "programming",
              "refactor", "unit test", "api", "devops"),
        theme=Theme("dark", "#0F1216", "#161B22", "#1C222B", "#2A313C", "#E8ECF2", "#AAB4C3", "#6F7A8A",
                    "#7AA2FF", "#0F1216", "#1F2A44", "Segoe UI Variable Display", 10),
        private_data="source code and credentials", audience="developers",
        primary=ModeSpec(
            "coder", "Code assistant", "What are you working on?", "Generate", "Result",
            "You are a precise coding assistant for {org}. Prefer short, correct code in fenced blocks and a "
            "one-line explanation. Never invent APIs.",
            ["Write a PowerShell function that lists the 5 largest files in a folder.",
             "Explain what this regex does: ^(?=.*\\d)(?=.*[a-z]).{8,}$",
             "Refactor into clean C#: for(int i=0;i<list.Count;i++){if(list[i]!=null){Console.WriteLine(list[i]);}}"]),
        default_drivers=("privacy", "cost", "offline")),
]

GENERAL = IndustryProfile(
    id="general", name="General",
    cues=(),
    theme=_light("#2F5BD3", "#E5ECFB", canvas="#F6F7F9", surface2="#EEF0F4", stroke="#DFE3EA",
                 ink="#171B22", ink2="#4A5160"),
    private_data="your documents and conversations", audience="people",
    primary=ModeSpec(
        "assistant", "Assistant", "What would you like help with?", "Go", "Result",
        "You are a helpful assistant for {org}. Answer in short sections or bullet points.",
        ["Summarise these notes into 3 actions: Met retail team. Store 12 needs new tills by Friday. Budget approved. Sam owns supplier call.",
         "Turn this into a clear customer email: 'delay on part, ETA 2 days, sorry, will call tomorrow'.",
         "Rewrite this to sound friendlier: 'Your request is denied pending review.'"]),
)


# ── Local AI drivers: why this app runs on the device ──────────────────────────

DRIVER_CUES: dict[str, tuple[str, ...]] = {
    "privacy": ("privacy", "private", "sensitive", "confidential", "personal", "guest data", "patient",
                "allerg", "medical", "without sending", "never leave", "not leave", "cloud", "gdpr", "pii",
                "customer data", "children", "salary"),
    "offline": ("offline", "connectivity", "poor internet", "no internet", "remote", "on site", "in the field",
                "airplane", "rural", "kiosk", "signal", "network"),
    "speed": ("instant", "real-time", "real time", "latency", "fast", "queue", "walk up", "walks up",
              "front desk", "at the desk", "live"),
    "cost": ("cost", "unmetered", "subscription", "per token", "per-token", "at scale", "budget", "free to run"),
    "compliance": ("compliance", "regulat", "sovereign", "residency", "audit", "hipaa", "fca", "legal hold"),
}

DRIVER_TEXT: dict[str, tuple[str, str]] = {
    "privacy": ("Private by design", "{private} stay on this PC. Nothing is sent to a cloud AI service."),
    "offline": ("Works without the internet", "Answers keep coming when Wi-Fi is poor or the network is down."),
    "speed": ("Instant at the point of need", "No round trip to a data centre, so {audience} get answers straight away."),
    "cost": ("No per-question cost", "Every answer runs on hardware you already own. No cloud AI bill per request."),
    "compliance": ("Easier compliance", "Data stays on a managed device, which simplifies data residency and audit."),
}


@dataclass
class LocalDriver:
    id: str
    title: str
    detail: str


def _hits(text: str, cues: tuple[str, ...]) -> int:
    # Word-start match: "store" must not match "stored", but "itinerar" matches "itinerary".
    return sum(1 for cue in cues if re.search(r"\b" + re.escape(cue.strip()), text))


def detect_industry(idea: str) -> IndustryProfile:
    text = idea.lower()
    best, best_hits = GENERAL, 0
    for profile in PROFILES:
        hits = _hits(text, profile.cues)
        if hits > best_hits:
            best, best_hits = profile, hits
    return best


def detect_drivers(idea: str, profile: IndustryProfile, count: int = 3) -> list[LocalDriver]:
    text = idea.lower()
    found = [d for d, cues in DRIVER_CUES.items() if any(c in text for c in cues)]
    ordered = found + [d for d in profile.default_drivers if d not in found]
    ordered += [d for d in DRIVER_TEXT if d not in ordered]
    drivers = []
    for d in ordered[:count]:
        title, detail = DRIVER_TEXT[d]
        private = profile.private_data[0].upper() + profile.private_data[1:]
        drivers.append(LocalDriver(d, title, detail.format(private=private, audience=profile.audience)))
    return drivers


# ── Ideas often contain the demo itself ─────────────────────────────────────────

_QUOTE = re.compile(r"[\"“”]([^\"“”]{25,400})[\"“”]")


def quoted_examples(idea: str) -> list[str]:
    """Example requests the presenter wrote into the idea, e.g. "We're a family of four...". Used first."""
    out = []
    for m in _QUOTE.finditer(idea):
        q = " ".join(m.group(1).split())
        if len(q.split()) >= 6 and q not in out:
            out.append(q)
    return out


def wants_staff_mode(idea: str, profile: IndustryProfile) -> bool:
    return profile.staff is not None and _hits(idea.lower(), profile.staff_cues) > 0


def wants_translation(idea: str) -> bool:
    return bool(re.search(r"translat|multilingual", idea.lower()))


def wants_knowledge(idea: str, profile: IndustryProfile) -> bool:
    text = idea.lower()
    return bool(re.search(r"\brag\b|knowledge|document|manual|polic|procedure|handbook|faq|catalog|"
                          r"downloaded|hotel-provided|guideline|sop\b|information", text)) or (
        profile.knowledge_pack is not None and profile.id != "general")


def is_staff_example(text: str) -> bool:
    return bool(re.search(r"\b(a guest|a customer|a patient|the guest|escalat|what should i check|"
                          r"walk me through|room \d{2,4})\b", text.lower()))


def theme_dict(theme: Theme) -> dict:
    d = asdict(theme)
    return {
        "mode": d["mode"], "canvas": d["canvas"], "surface": d["surface"], "surface2": d["surface2"],
        "stroke": d["stroke"], "ink": d["ink"], "ink2": d["ink2"], "muted": d["muted"],
        "accent": d["accent"], "accentInk": d["accent_ink"], "accentSoft": d["accent_soft"],
        "displayFont": d["display_font"], "corner": d["corner"],
    }
