"""
Zava Live Build -- stage narrator.

Console output designed to be read from the back of a room: a Zava banner,
numbered stage headers, a model-selection card and a Go/No-Go board.
Plain ASCII box drawing so it renders on any venue laptop and terminal font.
"""

from __future__ import annotations

import os
import sys
import textwrap

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
WHITE = "\033[97m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

WIDTH = 76

if os.name == "nt":
    os.system("")  # enables ANSI colour in classic Windows consoles
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def c(text: str, code: str) -> str:
    return f"{code}{text}{RESET}" if sys.stdout.isatty() else text


def banner(subtitle: str) -> None:
    line = "=" * WIDTH
    print()
    print(c(line, CYAN))
    print(c("  Z A V A", BOLD + WHITE) + c("   |   LIVE LOCAL AI BUILD", BOLD + CYAN))
    print(c(f"  {subtitle}", WHITE))
    print(c("  Built by Surface AI Factory", DIM))
    print(c(line, CYAN))


def stage(n: int, total: int, title: str) -> None:
    print()
    print(c(f"[{n}/{total}] {title.upper()}", BOLD + CYAN))
    print(c("-" * WIDTH, DIM))


def info(msg: str) -> None:
    print(f"  {msg}")


def ok(msg: str) -> None:
    print(c(f"  [OK]   {msg}", GREEN))


def warn(msg: str) -> None:
    print(c(f"  [WARN] {msg}", YELLOW))


def fail(msg: str) -> None:
    print(c(f"  [FAIL] {msg}", RED))


def card(title: str, rows: list[tuple[str, str]], footer: list[str] | None = None,
         colour: str = CYAN) -> None:
    inner = WIDTH - 4
    print(c("  +" + "-" * inner + "+", colour))
    print(c("  | ", colour) + c(title.ljust(inner - 2)[: inner - 2], BOLD + WHITE) + c(" |", colour))
    print(c("  +" + "-" * inner + "+", colour))
    key_w = max((len(k) for k, _ in rows), default=0)
    for key, value in rows:
        wrapped = textwrap.wrap(str(value), inner - key_w - 5) or [""]
        for i, part in enumerate(wrapped):
            label = key.ljust(key_w) if i == 0 else " " * key_w
            print(c("  | ", colour) + f"{label} : {part}".ljust(inner - 2) + c(" |", colour))
    if footer:
        print(c("  +" + "-" * inner + "+", colour))
        for line in footer:
            for i, part in enumerate(textwrap.wrap(line, inner - 4) or [""]):
                bullet = "- " if i == 0 else "  "
                print(c("  | ", colour) + f"{bullet}{part}".ljust(inner - 2)[: inner - 2] + c(" |", colour))
    print(c("  +" + "-" * inner + "+", colour))


def model_card(plan) -> None:
    p = plan.primary
    if p is None:
        fail("No NPU chat model is available on this device.")
        return
    ep = (p.execution_provider or "NPU").replace("ExecutionProvider", "")
    rows = [
        ("Model", p.alias),
        ("Pinned build", p.model_id),
        ("Runs on", f"NPU via {ep}"),
        ("Size", f"{p.size_mb / 1024:.2f} GB"),
        ("Cached", "yes, ready offline" if p.cached else "no, preflight downloads it"),
    ]
    if plan.backup:
        b = plan.backup
        rows.append(("Stage backup", f"{b.alias} ({b.model_id}{', cached' if b.cached else ''})"))
    rows.append(("Catalog", f"{plan.catalog_source} Foundry Local catalog"))
    card("MODEL SELECTION  (on-device, NPU-first)", rows, footer=p.reasons, colour=GREEN)


def go_board(results: list[tuple[str, str, str]]) -> bool:
    """results: (check, status PASS|WARN|FAIL, detail). Returns True for GO."""
    print()
    print(c("  SHOW READINESS", BOLD + WHITE))
    for name, status, detail in results:
        colour = {"PASS": GREEN, "WARN": YELLOW, "FAIL": RED}[status]
        print(c(f"  [{status}] ", colour) + name.ljust(34) + c(detail, DIM))
    go = not any(s == "FAIL" for _, s, _ in results)
    print()
    if go:
        print(c("  ****  GO FOR SHOW  ****", BOLD + GREEN))
    else:
        print(c("  ****  NO-GO: fix the FAIL items above before the session  ****", BOLD + RED))
    return go
