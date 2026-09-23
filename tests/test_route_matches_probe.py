"""The shipped route rva must be the one the game actually uses.

Why this is separate from test_static_chain:

That test builds a synthetic memory image and checks the chain's arithmetic -
given an rva, does it reach `value + position * 76`, and does it refuse a stale
route. It reads the rva from the module so the two stay consistent, which means
it cannot catch a WRONG rva: change the module to the previous build's offset
and the test builds its image around that offset too and still passes. Verified:
substituting the dead 0x2ac7cb0 leaves it at 14/14.

The rva is a fact about the game, not about our code, so it has to be pinned
against something outside the module. Two things are available offline:

  1. the probe transcripts in docs/, which record the rva and the address it
     pointed at on two separate launches. The table addresses differ (ASLR) and
     the rva does not - that is what makes it a route.
  2. the arithmetic that ties the route's target to a row the scan independently
     located, which is what the transcripts also record.

This test checks (1) and (2) against the module's current value, so a future
edit that pastes a stale rva - the exact mistake this build required fixing -
fails here with a message naming the offset.

Run:  python tests/test_route_matches_probe.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import dlbin_tables  # noqa: E402

EVIDENCE = ROOT / "data" / "route_evidence.json"

failures = 0
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global failures, checks
    checks += 1
    if cond:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label} {detail}")
        failures += 1


def module_routes() -> list[dict]:
    """The (rva, kind, name) entries in M.ROUTES, ignoring commented-out lines.

    Parsed from the table body rather than the whole file: the previous build's
    offsets are kept as comments nearby (they are useful when a route moves
    again), and a whole-file regex picks those up as if they were shipped.
    """
    src = (ROOT / "mod_template" / "src" / "16_static_chain.lua").read_text(
        encoding="utf-8")
    m = re.search(r"^M\.ROUTES\s*=\s*\{(.*?)^\}", src, re.S | re.M)
    if not m:
        return []
    body = m.group(1)
    out = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        for e in re.finditer(
                r'\{\s*rva\s*=\s*(0x[0-9a-fA-F]+)\s*,\s*kind\s*=\s*"(\w+)"\s*,\s*name\s*=\s*"(\w+)"',
                stripped):
            out.append({"rva": int(e.group(1), 16), "kind": e.group(2),
                        "name": e.group(3)})
    return out


def main() -> int:
    routes = module_routes()
    print("== the module's routes ==")
    for r in routes:
        print(f"  {hex(r['rva'])}  kind={r['kind']}  name={r['name']}")
    check("the module ships at least one route", len(routes) > 0, "none found")

    if not EVIDENCE.exists():
        print()
        print(f"  [SKIP] {EVIDENCE.relative_to(ROOT)} is missing, so the route")
        print("         cannot be checked against a measurement.")
        print()
        print("test_route_matches_probe: SKIP (no evidence file)")
        return 0

    doc = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    # The file stores addresses as hex strings so it stays readable and diffable;
    # convert once here rather than at every use.
    runs = [
        {**r,
         "table_base": int(r["table_base"], 16),
         "route_rva": int(r["route_rva"], 16),
         "route_value": int(r["route_value"], 16),
         "located_row": int(r["located_row"], 16)}
        for r in doc.get("runs", [])
    ]
    doc["dead_routes"] = [int(v, 16) for v in doc.get("dead_routes", [])]
    print()
    print(f"== the probe evidence ({len(runs)} run(s)) ==")
    for r in runs:
        print(f"  {r['when']}  table_base {r['table_base']}  "
              f"rva {r['route_rva']} -> {r['route_value']}")

    print()
    print("== the rva that repeated across runs is the route ==")
    rvas = {r["route_rva"] for r in runs}
    check("the probe reported the same rva on every run", len(rvas) == 1,
          f"found {sorted(hex(v) for v in rvas)}")
    if len(rvas) != 1:
        print("  (a route that does not repeat is not a route; the module value")
        print("   cannot be justified from this evidence)")
        print()
        print(f"test_route_matches_probe: FAIL ({failures} of {checks} failed)")
        return 1
    measured = rvas.pop()

    check("the module ships that rva",
          any(r["rva"] == measured for r in routes),
          f"module has {[hex(r['rva']) for r in routes]}, probe measured "
          f"{hex(measured)}")

    print()
    print("== the route's target agrees with an independent mechanism ==")
    # Each run records where the route pointed and where the scan independently
    # located a known row. The two must satisfy `value + position * 76 == row`.
    position = doc["position"]
    stride = dlbin_tables.DAMAGE_RECORD_SIZE
    for r in runs:
        predicted = r["route_value"] + position * stride
        check(f"{r['when']}: route + {position}*{stride} == the scanned row",
              predicted == r["located_row"],
              f"{hex(predicted)} vs {hex(r['located_row'])}")

    print()
    print("== the table moved between runs (so the rva is what is stable) ==")
    bases = {r["table_base"] for r in runs}
    check("the table address differs across runs", len(bases) > 1,
          f"all {len(runs)} run(s) reported {hex(next(iter(bases)))} - with one "
          f"run this cannot be shown, and with equal addresses ASLR did not vary")

    print()
    print("== the dead routes are not shipped ==")
    # The previous build's offsets. Shipping one would cost a read and a log
    # line per launch for a value that cannot resolve.
    dead = doc.get("dead_routes", [])
    for rva in dead:
        check(f"{hex(rva)} (previous build) is not shipped",
              not any(r["rva"] == rva for r in routes),
              "a stale route is in M.ROUTES")

    print()
    if failures == 0:
        print(f"test_route_matches_probe: PASS ({checks} checks)")
        return 0
    print(f"test_route_matches_probe: FAIL ({failures} of {checks} failed)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
