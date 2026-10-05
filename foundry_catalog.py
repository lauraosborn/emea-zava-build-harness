"""
Zava Live Build -- Foundry Local catalog reader.

Reads the model catalog the device can actually run, in order of preference:

  1. live   - the running Foundry Local service (/foundry/list + /openai/models)
  2. cli    - parsing `foundry model list` / `foundry cache list`
  3. snapshot - data/foundry_catalog_snapshot.json bundled with the harness

Every layer degrades to the next one, so model selection on stage never fails
just because the service is slow to start or the venue Wi-Fi is down.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).parent / "data" / "foundry_catalog_snapshot.json"

NPU_EXECUTION_PROVIDERS = ("OpenVINOExecutionProvider", "QNNExecutionProvider", "VitisAIExecutionProvider")


@dataclass
class ModelVariant:
    id: str                       # full id incl. version, e.g. phi-4-mini-instruct-openvino-npu:4
    alias: str                    # catalog alias, e.g. phi-4-mini
    device: str                   # NPU | GPU | CPU
    task: str                     # chat-completion | automatic-speech-recognition | ...
    size_mb: int
    license: str = ""
    execution_provider: str = ""
    publisher: str = ""
    tool_calling: bool = False
    max_output_tokens: int | None = None
    cached: bool = False

    @property
    def version(self) -> str:
        return self.id.rsplit(":", 1)[1] if ":" in self.id else ""

    @property
    def size_gb(self) -> float:
        return round(self.size_mb / 1024, 2)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["version"] = self.version
        return data


@dataclass
class Catalog:
    source: str                              # live | cli | snapshot
    variants: list[ModelVariant] = field(default_factory=list)
    endpoint: str | None = None              # service base URL when live
    execution_providers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def by_alias(self, alias: str) -> list[ModelVariant]:
        return [v for v in self.variants if v.alias.lower() == alias.lower()]

    def npu(self, task: str | None = None) -> list[ModelVariant]:
        return [v for v in self.variants
                if v.device == "NPU" and (task is None or v.task == task)]

    def find(self, model_id: str) -> ModelVariant | None:
        for v in self.variants:
            if v.id.lower() == model_id.lower():
                return v
        return None

    @property
    def npu_execution_provider(self) -> str | None:
        for ep in self.execution_providers:
            if ep in NPU_EXECUTION_PROVIDERS:
                return ep
        eps = {v.execution_provider for v in self.variants if v.device == "NPU" and v.execution_provider}
        return sorted(eps)[0] if eps else None


# ── Foundry CLI helpers ─────────────────────────────────────────────────────

def foundry_cli() -> str | None:
    return shutil.which("foundry")


def _run_cli(*args: str, timeout: int = 90) -> str:
    exe = foundry_cli()
    if not exe:
        return ""
    try:
        proc = subprocess.run([exe, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        return (proc.stdout or "") + (proc.stderr or "")
    except (subprocess.SubprocessError, OSError):
        return ""


def service_status() -> tuple[str | None, list[str]]:
    """Return (base_url, execution_providers) from `foundry service status`."""
    out = _run_cli("service", "status", timeout=60)
    return parse_service_status(out)


def parse_service_status(text: str) -> tuple[str | None, list[str]]:
    url = None
    m = re.search(r"(https?://[\w.\-]+:\d+)", text)
    if m:
        url = m.group(1)
    eps: list[str] = []
    m = re.search(r"Valid EPs:\s*(.+)", text)
    if m:
        eps = [e.strip() for e in m.group(1).split(",") if e.strip()]
    return url, eps


def start_service() -> tuple[str | None, list[str]]:
    _run_cli("service", "start", timeout=120)
    return service_status()


# ── Layer 1: live service ───────────────────────────────────────────────────

def _variant_from_service(entry: dict, cached_ids: set[str]) -> ModelVariant | None:
    runtime = entry.get("runtime") or {}
    model_id = entry.get("name") or ""
    if not model_id:
        return None
    return ModelVariant(
        id=model_id,
        alias=entry.get("alias") or model_id.split(":")[0],
        device=(runtime.get("deviceType") or "CPU").upper(),
        task=entry.get("task") or "",
        size_mb=int(entry.get("fileSizeMb") or 0),
        license=entry.get("license") or "",
        execution_provider=runtime.get("executionProvider") or "",
        publisher=entry.get("publisher") or "",
        tool_calling=bool(entry.get("supportsToolCalling")),
        max_output_tokens=entry.get("maxOutputTokens"),
        cached=model_id in cached_ids,
    )


def load_live(base_url: str, timeout: int = 20) -> list[ModelVariant]:
    import requests

    listing = requests.get(f"{base_url}/foundry/list", timeout=timeout).json()
    try:
        cached = set(requests.get(f"{base_url}/openai/models", timeout=timeout).json())
    except Exception:
        cached = set()
    variants = [_variant_from_service(e, cached) for e in listing]
    return [v for v in variants if v]


# ── Layer 2: CLI table parsing ──────────────────────────────────────────────

_ROW = re.compile(
    r"^(?P<alias>\S+)?\s+(?P<device>NPU|GPU|CPU)\s+(?P<task>\S+)\s+"
    r"(?P<size>[\d.]+)\s*(?P<unit>[GM]B)\s+(?P<license>\S+)\s+(?P<id>\S+)\s*$"
)


def parse_model_list(text: str) -> list[ModelVariant]:
    variants: list[ModelVariant] = []
    alias = None
    for line in text.splitlines():
        m = _ROW.match(line.rstrip())
        if not m:
            continue
        alias = m.group("alias") or alias
        if not alias:
            continue
        size = float(m.group("size"))
        size_mb = int(size * 1024) if m.group("unit") == "GB" else int(size)
        device = m.group("device")
        model_id = m.group("id")
        ep = ""
        if device == "NPU":
            ep = ("OpenVINOExecutionProvider" if "openvino" in model_id.lower()
                  else "QNNExecutionProvider" if "qnn" in model_id.lower()
                  else "VitisAIExecutionProvider" if "vitis" in model_id.lower() else "")
        variants.append(ModelVariant(id=model_id, alias=alias, device=device,
                                     task=m.group("task"), size_mb=size_mb,
                                     license=m.group("license"), execution_provider=ep))
    return variants


_MODEL_ID = re.compile(r"^[\w.\-]+:\d+$")


def parse_cache_list(text: str) -> set[str]:
    ids = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and _MODEL_ID.match(parts[-1]):
            ids.add(parts[-1])
    return ids


def load_cli() -> list[ModelVariant]:
    variants = parse_model_list(_run_cli("model", "list", timeout=180))
    cached = parse_cache_list(_run_cli("cache", "list", timeout=60))
    for v in variants:
        v.cached = v.id in cached
    return variants


# ── Layer 3: bundled snapshot ───────────────────────────────────────────────

def load_snapshot(path: Path = SNAPSHOT_PATH) -> list[ModelVariant]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    entries = data.get("models", data) if isinstance(data, dict) else data
    variants = []
    for e in entries:
        if "runtime" in e or "name" in e:
            v = _variant_from_service(e, set())
        else:
            v = ModelVariant(**{k: e[k] for k in ModelVariant.__dataclass_fields__ if k in e})
            v.cached = False
        if v:
            variants.append(v)
    return variants


def write_snapshot(catalog: Catalog, path: Path = SNAPSHOT_PATH) -> None:
    """Refresh the bundled snapshot from a live catalog (cached flags are device-specific, so dropped)."""
    import datetime as _dt
    models = []
    for v in catalog.variants:
        d = v.to_dict()
        d.pop("cached", None)
        d.pop("version", None)
        models.append(d)
    path.write_text(json.dumps({
        "capturedOn": _dt.datetime.now().isoformat(timespec="seconds"),
        "source": catalog.source,
        "models": models,
    }, indent=1), encoding="utf-8")


# ── Public entry point ──────────────────────────────────────────────────────

def load_catalog(allow_live: bool = True, start_if_needed: bool = True) -> Catalog:
    notes: list[str] = []
    eps: list[str] = []

    if allow_live and foundry_cli():
        url, eps = service_status()
        if not url and start_if_needed:
            notes.append("Foundry Local service was not running; started it.")
            url, eps = start_service()
        if url:
            try:
                variants = load_live(url)
                if variants:
                    return Catalog("live", variants, url, eps, notes)
            except Exception as ex:  # noqa: BLE001 - any failure drops to the next layer
                notes.append(f"Live catalog unavailable ({ex.__class__.__name__}); using CLI.")
        variants = load_cli()
        if variants:
            return Catalog("cli", variants, url, eps, notes)
        notes.append("Foundry CLI returned no models; using bundled snapshot.")
    elif allow_live:
        notes.append("Foundry Local CLI not found on PATH; using bundled snapshot.")

    return Catalog("snapshot", load_snapshot(), None, eps, notes)
