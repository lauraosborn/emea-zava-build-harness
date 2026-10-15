"""Unit tests for the Zava Live Build event harness. Run: python -m unittest discover tests"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ai_assist  # noqa: E402
import foundry_catalog as fc  # noqa: E402
from event_builder import create_event_app  # noqa: E402
from event_checks import check_event_readiness  # noqa: E402
from guardrails import GuardrailRegistry, capability_gap_options, model_not_cached_options  # noqa: E402
from model_selector import (  # noqa: E402
    analyse_idea, default_app_spec, select_models, title_from_idea)

CLI_TABLE = """Alias                          Device     Task               File Size    License      Model ID
-----------------------------------------------------------------------------------------------
phi-4-mini                     NPU        chat-completion    2.15 GB      MIT          phi-4-mini-instruct-openvino-npu:4
                               GPU        chat-completion    2.15 GB      MIT          phi-4-mini-instruct-openvino-gpu:2
                               CPU        chat-completion    4.80 GB      MIT          Phi-4-mini-instruct-generic-cpu:5
------------------------------------------------------------------------------------------------------------------------
qwen3-vl-2b-instruct           CPU        vision-language-chat 1.34 GB      apache-2.0   qwen3-vl-2b-instruct-generic-cpu:1
"""


def variant(alias, device="NPU", task="chat-completion", size=1000, cached=False, version=1):
    return fc.ModelVariant(id=f"{alias}-{device.lower()}:{version}", alias=alias, device=device, task=task,
                           size_mb=size, license="MIT", execution_provider="OpenVINOExecutionProvider"
                           if device == "NPU" else "", cached=cached)


def catalog(*variants):
    return fc.Catalog("snapshot", list(variants))


class CatalogParsingTests(unittest.TestCase):
    def test_parses_cli_table_including_continuation_rows_and_wide_tasks(self):
        rows = fc.parse_model_list(CLI_TABLE)
        self.assertEqual(len(rows), 4)
        self.assertEqual({r.alias for r in rows}, {"phi-4-mini", "qwen3-vl-2b-instruct"})
        npu = [r for r in rows if r.device == "NPU"][0]
        self.assertEqual(npu.id, "phi-4-mini-instruct-openvino-npu:4")
        self.assertEqual(npu.size_mb, int(2.15 * 1024))
        self.assertEqual(npu.execution_provider, "OpenVINOExecutionProvider")
        self.assertEqual(rows[-1].task, "vision-language-chat")

    def test_parses_service_status(self):
        url, eps = fc.parse_service_status(
            "Model management service is running on http://127.0.0.1:56819/openai/status\n"
            "Valid EPs: CPUExecutionProvider, OpenVINOExecutionProvider")
        self.assertEqual(url, "http://127.0.0.1:56819")
        self.assertIn("OpenVINOExecutionProvider", eps)

    def test_parses_cache_list(self):
        ids = fc.parse_cache_list("Models cached on device:\n   Alias   Model ID\n"
                                  "x phi-4-mini   phi-4-mini-instruct-openvino-npu:4\n")
        self.assertEqual(ids, {"phi-4-mini-instruct-openvino-npu:4"})

    def test_bundled_snapshot_loads_and_has_npu_models(self):
        variants = fc.load_snapshot()
        self.assertTrue(any(v.device == "NPU" and v.task == "chat-completion" for v in variants))
        self.assertFalse(any(v.cached for v in variants), "snapshot must never claim models are cached")


class ModelSelectionTests(unittest.TestCase):
    def test_documents_idea_picks_phi_on_npu_with_small_backup(self):
        cat = catalog(variant("phi-4-mini", size=2200, cached=True),
                      variant("qwen2.5-0.5b", size=330, cached=True),
                      variant("phi-4-mini", device="GPU", size=2200, cached=True))
        plan = select_models(analyse_idea("Summarise meeting notes into actions"), cat)
        self.assertEqual(plan.primary.alias, "phi-4-mini")
        self.assertEqual(plan.primary.device, "NPU")
        self.assertEqual(plan.backup.alias, "qwen2.5-0.5b")

    def test_code_idea_prefers_coder_model(self):
        cat = catalog(variant("phi-4-mini", cached=True), variant("qwen2.5-coder-1.5b", cached=True))
        plan = select_models(analyse_idea("Explain a PowerShell script to a developer"), cat)
        self.assertEqual(plan.primary.alias, "qwen2.5-coder-1.5b")

    def test_best_fit_wins_even_when_not_cached_and_cached_alternative_is_offered(self):
        from model_selector import best_cached_alternative
        cat = catalog(variant("phi-4-mini", cached=False), variant("qwen2.5-1.5b", cached=True))
        analysis = analyse_idea("a general helper")
        plan = select_models(analysis, cat)
        self.assertEqual(plan.primary.alias, "phi-4-mini")
        self.assertFalse(plan.primary.cached)
        self.assertEqual([m.alias for m in plan.models_to_cache], ["phi-4-mini"])
        self.assertEqual(best_cached_alternative(cat, "phi-4-mini", analysis).alias, "qwen2.5-1.5b")

    def test_reasoning_distilled_models_are_never_stage_primaries(self):
        cat = catalog(variant("phi-4-mini-reasoning", cached=True), variant("qwen2.5-1.5b"))
        plan = select_models(analyse_idea("solve maths problems with step by step logic"), cat)
        self.assertEqual(plan.primary.alias, "qwen2.5-1.5b")

    def test_gpu_only_catalog_yields_no_primary(self):
        plan = select_models(analyse_idea("chat"), catalog(variant("phi-4", device="GPU")))
        self.assertIsNone(plan.primary)

    def test_pinned_alias_wins_when_it_has_an_npu_build(self):
        cat = catalog(variant("phi-4-mini", cached=True), variant("qwen2.5-0.5b", cached=True))
        plan = select_models(analyse_idea("notes"), cat, pin_alias="qwen2.5-0.5b")
        self.assertEqual(plan.primary.alias, "qwen2.5-0.5b")

    def test_speech_idea_without_npu_speech_model_reports_gap(self):
        cat = catalog(variant("phi-4-mini", cached=True),
                      variant("whisper-small", device="CPU", task="automatic-speech-recognition"))
        plan = select_models(analyse_idea("transcribe voice notes from engineers"), cat)
        self.assertEqual([g.capability for g in plan.gaps], ["speech"])
        self.assertIn("whisper-small", plan.gaps[0].best_available)

    def test_title_from_idea(self):
        self.assertEqual(title_from_idea("Build a local AI app that helps nurses hand over shifts"),
                         "Nurses Hand Over Assistant")


class AssistParsingTests(unittest.TestCase):
    def test_parses_fenced_json(self):
        spec = ai_assist.parse_spec('```json\n{"title":"Zava FieldNote","tagline":"t","input_label":"Notes",'
                                    '"sample_prompts":["a","b","c"]}\n```')
        self.assertEqual(spec.title, "FieldNote")
        self.assertEqual(len(spec.sample_prompts), 3)

    def test_rejects_invalid_json(self):
        self.assertIsNone(ai_assist.parse_spec("Sure! Here is your app: FieldNote"))


class GuardrailTests(unittest.TestCase):
    def test_non_interactive_defaults_are_safe(self):
        reg = GuardrailRegistry(interactive=False)
        a = reg.event("model-cached", "r", "x", model_not_cached_options("m:1", 2.0, "qwen2.5-1.5b"))
        b = reg.event("npu-speech", "r", "x", capability_gap_options("speech", "whisper (CPU)"))
        c = reg.event("model-cached", "r", "x", model_not_cached_options("pinned:1", 1.0, None))
        reg.resolve_all()
        self.assertEqual(a.resolution, "cached", "unattended runs must avoid downloads when a cached model exists")
        self.assertEqual(b.resolution, "text-first")
        self.assertEqual(c.resolution, "download", "a pinned model with no alternative is downloaded in preflight")
        self.assertFalse(reg.aborted)


class GenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cat = catalog(variant("phi-4-mini", size=2200, cached=True), variant("qwen2.5-0.5b", size=330, cached=True))
        idea = "Turn rough job notes into a work order"
        analysis = analyse_idea(idea)
        cls.plan = select_models(analysis, cat)
        cls.spec = default_app_spec(idea, analysis, name="Job Notes Studio")
        cls.root = create_event_app(cls.spec, cls.plan, "job-notes-studio", cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_creates_buildable_project_layout(self):
        project = self.root / "src" / "JobNotesStudio"
        for rel in ("JobNotesStudio.csproj", "MainWindow.xaml", "Services/LocalAiRuntime.cs",
                    "Services/InferenceHardwareSelector.cs", "Views/AboutPage.xaml",
                    "Branding/BrandingConstants.cs", "PresenterKeyHook.cs", "Assets/zava-logo.png"):
            self.assertTrue((project / rel).exists(), rel)
        self.assertTrue((self.root / "JobNotesStudio.sln").exists())
        self.assertTrue((self.root / "docs" / "DEMO-RUNBOOK.md").exists())

    def test_no_template_tokens_left_behind(self):
        for f in self.root.rglob("*"):
            if f.is_file() and f.suffix in (".cs", ".xaml", ".csproj", ".sln", ".ps1", ".json", ".md", ".appxmanifest"):
                text = f.read_text(encoding="utf-8")
                self.assertNotIn("__APP", text, str(f))
                self.assertNotIn("NAMESPACE", text, str(f))

    def test_app_config_pins_exact_npu_builds(self):
        cfg = json.loads((self.root / "src/JobNotesStudio/Assets/app-config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["primary"]["modelId"], self.plan.primary.model_id)
        self.assertIn(":", cfg["primary"]["modelId"])
        self.assertEqual(cfg["backup"]["alias"], "qwen2.5-0.5b")
        self.assertEqual(cfg["samplePrompts"], self.spec.sample_prompts)

    def test_event_readiness_has_no_failures_before_preflight(self):
        section = check_event_readiness(self.root)
        failures = [f.rule for f in section.findings if f.severity == "FAIL"]
        self.assertEqual(failures, [])
        self.assertIn("preflight", [f.rule for f in section.findings if f.severity == "WARN"])

    def test_app_first_design_checks_pass(self):
        section = check_event_readiness(self.root)
        passed = {f.rule for f in section.findings if f.severity == "PASS"}
        for rule in ("app-first-wording", "industry-design", "local-ai-story", "secondary-tech-details"):
            self.assertIn(rule, passed)

    def test_field_service_idea_gets_field_profile_and_no_npu_wording(self):
        cfg = json.loads((self.root / "src/JobNotesStudio/Assets/app-config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["industry"], "field")
        self.assertNotIn("NPU", cfg["tagline"])
        self.assertTrue(cfg["localDrivers"])
        self.assertTrue(cfg["theme"]["accent"].startswith("#"))


HOTEL_IDEA = ("Local AI Hotel Concierge: an on-device AI concierge for hotels that helps guests and hotel staff "
              "without sending sensitive guest information to the cloud. A guest walks up to a kiosk, or staff use "
              "a Surface at reception and say: \"We're a family of four. Suggest a rainy-day itinerary nearby, find "
              "somewhere suitable for dinner, and explain how we get there.\" Multilingual Q&A and translation. "
              "Staff ask: \"A guest in room 214 says the air conditioning isn't working. What should I check "
              "before escalating it?\" Local RAG over hotel knowledge.")


class IndustryDesignTests(unittest.TestCase):
    def test_hotel_idea_becomes_a_hospitality_concierge(self):
        spec = default_app_spec(HOTEL_IDEA, analyse_idea(HOTEL_IDEA))
        self.assertEqual(spec.industry, "hospitality")
        self.assertEqual(spec.title, "Hotel Concierge")
        self.assertNotIn("NPU", spec.tagline)
        names = [m["name"] for m in spec.modes]
        self.assertEqual(names, ["Guest concierge", "Staff assistant", "Translate"])
        self.assertTrue(spec.modes[0]["samplePrompts"][0].startswith("We're a family of four"))
        self.assertTrue(spec.modes[1]["samplePrompts"][0].startswith("A guest in room 214"))
        self.assertEqual(spec.local_drivers[0]["id"], "privacy")
        self.assertTrue(spec.knowledge)
        self.assertEqual(spec.knowledge_pack, "hospitality")

    def test_hotel_app_ships_knowledge_pack(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            cat = catalog(variant("phi-4-mini", size=2200, cached=True), variant("qwen2.5-0.5b", size=330, cached=True))
            analysis = analyse_idea(HOTEL_IDEA)
            spec = default_app_spec(HOTEL_IDEA, analysis)
            root = create_event_app(spec, select_models(analysis, cat), "hotel-concierge", tmp)
            kb = root / "src" / "HotelConcierge" / "Assets" / "knowledge"
            self.assertTrue((kb / "public" / "hotel-policies.md").exists())
            self.assertTrue((kb / "staff" / "maintenance-hvac.md").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_manufacturing_quality_idea_gets_custom_inspection_app(self):
        idea = ("AI Quality Inspector for a manufacturing factory. Operators inspect component photos, diagnose "
                "surface defects, use Tool Station 4 telemetry and retrieve offline calibration procedures.")
        spec = default_app_spec(idea, analyse_idea(idea))
        self.assertEqual(spec.industry, "field")
        self.assertEqual(spec.title, "AI Quality Inspector")
        self.assertEqual(spec.organisation, "Zava Manufacturing")
        self.assertEqual([m["name"] for m in spec.modes], ["Inspect", "Diagnose", "Assist"])
        self.assertEqual(spec.context_title, "Production context")
        self.assertTrue(spec.knowledge)
        self.assertEqual(spec.knowledge_pack, "manufacturing-quality")
        self.assertIn("Tool Station 4", spec.modes[2]["samplePrompts"][0])
        self.assertEqual(spec.local_drivers[1]["id"], "privacy")

    def test_blueprint_app_ships_use_case_knowledge_pack(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            cat = catalog(variant("phi-4-mini", size=2200, cached=True), variant("qwen2.5-0.5b", size=330, cached=True))
            idea = ("Build an AI Quality Inspector for manufacturing quality inspection. Inspect component photos, "
                    "diagnose defects, use fault telemetry and show tool calibration procedures.")
            analysis = analyse_idea(idea)
            spec = default_app_spec(idea, analysis)
            root = create_event_app(spec, select_models(analysis, cat), "ai-quality-inspector", tmp)
            kb = root / "src" / "AIQualityInspector" / "Assets" / "knowledge"
            self.assertTrue((kb / "public" / "component-reference.md").exists())
            self.assertTrue((kb / "staff" / "maintenance-manual.md").exists())
            self.assertTrue((kb / "staff" / "fault-codes-and-telemetry.md").exists())
            cfg = json.loads((root / "src/AIQualityInspector/Assets/app-config.json").read_text(encoding="utf-8"))
            self.assertEqual([m["name"] for m in cfg["modes"]], ["Inspect", "Diagnose", "Assist"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_healthcare_patient_companion_gets_custom_aftercare_app(self):
        idea = ("Patient Companion for a healthcare discharge journey. It answers patient aftercare questions "
                "from clinician-approved care plan guidance while offline.")
        spec = default_app_spec(idea, analyse_idea(idea))
        self.assertEqual(spec.title, "Patient Companion")
        self.assertEqual([m["name"] for m in spec.modes], ["Aftercare Q&A", "Visit prep"])
        self.assertEqual(spec.knowledge_pack, "patient-companion")
        self.assertEqual(spec.local_drivers[0]["id"], "privacy")

    def test_unknown_industry_falls_back_to_general(self):
        idea = "Help me rewrite messages to be friendlier"
        spec = default_app_spec(idea, analyse_idea(idea))
        self.assertEqual(spec.industry, "general")
        self.assertEqual(len(spec.modes), 1)


class LocalStorageTests(unittest.TestCase):
    def test_default_output_is_under_the_user_profile_not_onedrive(self):
        from event_flow import LOCAL_APPS_DIR, is_cloud_synced, local_work_dir
        self.assertEqual(local_work_dir(None), LOCAL_APPS_DIR)
        self.assertEqual(LOCAL_APPS_DIR.parent, Path.home())
        self.assertTrue(is_cloud_synced(Path(r"C:\Users\x\OneDrive - Contoso\Documents")))
        self.assertEqual(local_work_dir(r"C:\Users\x\OneDrive - Contoso\builds"), LOCAL_APPS_DIR)


if __name__ == "__main__":
    unittest.main()
