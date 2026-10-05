"""
Zava Live Build -- local AI assist.

Uses a small model on the NPU (through the Foundry Local service) to tailor
the generated app: title, tagline, system prompt and sample prompts. This
lets the presenter say "the factory itself runs on the NPU too".

It is strictly optional. Any failure (service down, model slow, malformed
JSON) returns None and the harness falls back to the deterministic spec.
"""

from __future__ import annotations

import json
import re
import time

from model_selector import AppSpec, IdeaAnalysis

PROMPT = """You design on-device AI demo apps for Zava, a fictitious company.
App idea: "{idea}"

Return ONLY a JSON object with these keys:
  "title": a 2-4 word product name (no company name),
  "tagline": one sentence describing what the app does,
  "input_label": a short label for the main text box,
  "sample_prompts": exactly 3 realistic requests a presenter would type into this app. Each must be
    1-3 sentences and include the actual sample data inline (names, times, quantities, problems), so
    the app has something concrete to work on.
Use fictitious names only."""


def tailor_app_spec(idea: str, analysis: IdeaAnalysis, endpoint: str, model_id: str,
                    timeout: int = 120, echo=None) -> tuple[AppSpec | None, str]:
    """Return (spec or None, status message). `echo(text)` receives streamed tokens for the stage."""
    try:
        import requests
    except ImportError:
        return None, "requests not installed"

    started = time.perf_counter()
    chunks: list[str] = []
    try:
        with requests.post(
            f"{endpoint}/v1/chat/completions",
            json={
                "model": model_id,
                "messages": [{"role": "user", "content": PROMPT.format(idea=idea.replace('"', "'"))}],
                "max_tokens": 600,
                "temperature": 0.3,
                "stream": True,
            },
            timeout=timeout,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines(decode_unicode=True):
                if time.perf_counter() - started > timeout:
                    raise TimeoutError("assist exceeded its time budget")
                if not raw or not raw.startswith("data:"):
                    continue
                payload = raw[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    delta = json.loads(payload)["choices"][0].get("delta", {}).get("content") or ""
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta:
                    chunks.append(delta)
                    if echo:
                        echo(delta)
    except Exception as ex:  # noqa: BLE001
        return None, f"local model unavailable ({ex.__class__.__name__})"

    elapsed = time.perf_counter() - started
    spec = parse_spec("".join(chunks))
    if spec is None:
        return None, "local model reply was not valid JSON"
    spec.generated_by = f"{model_id} on the NPU in {elapsed:.0f}s"
    return spec, spec.generated_by


def parse_spec(text: str) -> AppSpec | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    samples = [str(s).strip() for s in data.get("sample_prompts") or [] if str(s).strip()]
    title = str(data.get("title") or "").strip().strip('"')
    if not title or len(samples) < 2:
        return None
    title = re.sub(r"^zava\s+", "", title, flags=re.IGNORECASE)[:40]
    # The system prompt stays deterministic (see model_selector.default_app_spec);
    # small models write unreliable system prompts, but good titles and samples.
    return AppSpec(
        title=title,
        tagline=str(data.get("tagline") or "").strip()[:160],
        system_prompt="",
        input_label=str(data.get("input_label") or "What would you like help with?").strip()[:60],
        sample_prompts=samples[:3],
    )
