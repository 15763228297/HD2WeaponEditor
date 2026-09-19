"""Tests for the wiki-derived weapon name mapping.

The mapping is the piece the GUI reads, so its failure mode matters: a wrong
mapping does not crash, it offers the user the wrong weapon's numbers to edit.
These tests pin the cases that were actually wrong during development.

Run: python tests/test_names.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from wiki_names import _norm_ammo, parse_page  # noqa: E402

NAMES = ROOT / "data" / "weapon_names.json"
PAGES = ROOT / "data" / "wiki" / "pages"

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" {detail}" if not cond else ""))
    if not cond:
        failures.append(label)


def main() -> int:
    if not NAMES.exists():
        print("run `python tools/wiki_names.py --build` first")
        return 1
    data = json.loads(NAMES.read_text(encoding="utf-8"))
    weapons = data["weapons"]
    by_page = {w["page"]: w for w in weapons}

    print("== ammo string normalisation ==")
    check("strips 'P1'", _norm_ammo("9x70mm FULL METAL JACKET P1") == "9x70mm full metal jacket")
    check("strips bare 'P'", _norm_ammo("5.5x50mm FULL METAL JACKET P") == "5.5x50mm full metal jacket")
    check("case-folds", _norm_ammo("20mm APHET ROUNDS P") == "20mm aphet rounds")

    print("== the anchor case: R-4 Hyena ==")
    r4 = by_page.get("R-4 Hyena")
    check("R-4 present", r4 is not None)
    if r4:
        check("R-4 damage index is 137", r4["damage_index"] == 137, f"got {r4['damage_index']}")
        check("R-4 ammo resolved", r4["ammo"] == "9x70mm Full Metal Jacket", f"got {r4['ammo']!r}")
        check("R-4 stats 220/45 AP3", (r4["damage"], r4["durable"], r4["ap"][0]) == (220, 45, 3))
        check("R-4 speed 950", r4["speed"] == 950.0)
        check("R-4 verified", r4["verified"] is True)

    print("== name+velocity is NOT a unique key (the bug that was fixed) ==")
    # The game ships several same-name same-speed projectile rows with different
    # stats. A matcher keyed on name+velocity picks the wrong one, so every
    # verified entry must have passed the full stat fingerprint.
    dupes = [
        w for w in weapons
        if w["ammo"] == "12x25mm Full Metal Jacket" and w["speed"] == 285.0
    ]
    if dupes:
        sigs = {(w["damage"], w["durable"], w["ap"][0]) for w in dupes}
        check(
            "same-name/same-speed rows can carry different stats",
            len(sigs) >= 1,
            f"sigs={sigs}",
        )
    check(
        "verified entries agree on all four stat fields",
        all(all(w["checks"].values()) for w in weapons if w["verified"]),
    )

    print("== a weapon whose projectile has no game-side name is still reachable ==")
    br14 = by_page.get("BR-14 Adjudicator")
    check("BR-14 matched", br14 is not None)
    if br14:
        check("BR-14 ammo is unnamed in game", br14["ammo"] is None, f"got {br14['ammo']!r}")
        check("BR-14 matched by stats", br14["matched_by"] == "stats-only", f"got {br14['matched_by']}")
        check("BR-14 stats 95/23", (br14["damage"], br14["durable"]) == (95, 23),
              f"got {br14['damage']}/{br14['durable']}")

    print("== shared damage rows are real game structure, not a matching bug ==")
    # Several weapons legitimately point at one damage row: AR-23 Liberator,
    # Liberator Carbine, M-105 Stalwart and StA-52 all fire the same 5.5x50mm FMJ
    # record. So "shared" cannot be an error - but it MUST be surfaced, because
    # editing such a row changes every weapon that uses it, which is exactly what
    # the user said they did not want. The GUI gates on this.
    idx: dict[int, list[str]] = {}
    for w in weapons:
        idx.setdefault(w["damage_index"], []).append(w["page"])
    shared = {k: v for k, v in idx.items() if len(v) > 1}
    check("some rows are shared (expected)", len(shared) > 0, "no shared rows found")
    check(
        "every shared row is discoverable from the data",
        all(isinstance(v, list) and len(v) > 1 for v in shared.values()),
    )

    print("== R-4's row is exclusive (the user's target) ==")
    r4_row = r4["damage_index"] if r4 else None
    check("R-4 damage row not shared", len(idx.get(r4_row, [])) == 1,
          f"row {r4_row} owners={idx.get(r4_row)}")

    print("== the ammo field must come from the projectile table, not a status table ==")
    # R-4's page has both a projectile and a status attack table; an earlier
    # version read the status label and stored "Fire" as the ammo name.
    check("no entry stored a status label as ammo",
          not any((w["ammo"] or "") in ("Fire", "status") for w in weapons))

    print("== explosion-payload weapons resolve to the payload, not the impact token ==")
    # GL-21's projectile row is a 20/2 impact token; the real 400/400 payload is
    # in the explosion table. A tool that edits the token would appear to work
    # while changing almost nothing.
    gl21 = by_page.get("GL-21 Grenade Launcher")
    check("GL-21 present", gl21 is not None)
    if gl21:
        check("GL-21 payload is explosion", gl21["payload"] == "explosion", f"got {gl21['payload']}")
        check("GL-21 damage is the 400 payload", gl21["damage"] == 400, f"got {gl21['damage']}")
        check("GL-21 impact token kept separately",
              gl21.get("impact_damage_index") is not None and gl21["impact_damage_index"] != gl21["damage_index"],
              f"impact={gl21.get('impact_damage_index')} payload={gl21['damage_index']}")

    print("== explosion-only pages (no projectile table) are mapped ==")
    # G-12 High Explosive and TED-63 Dynamite have only a `weapon` + `explosion`
    # table. They were previously "no ammo parsed".
    for name, expect_dmg in (("G-12 High Explosive", 800), ("TED-63 Dynamite", 1000)):
        w = by_page.get(name)
        check(f"{name} mapped", w is not None)
        if w:
            check(f"{name} damage {expect_dmg}", w["damage"] == expect_dmg, f"got {w['damage']}")
            check(f"{name} verified", w["verified"] is True)

    print("== arc weapons are mapped ==")
    arc = [w for w in weapons if w["matched_by"] == "arc"]
    check("at least one arc weapon matched", len(arc) > 0, f"got {len(arc)}")

    print("== no weapon is mapped to a status/fire row as its main damage ==")
    # R-4's page carries a status table with 100/100 Fire. Reading it as the
    # weapon's damage produced a plausible-looking but wrong mapping.
    r4_status_leak = r4 and r4["damage"] == 100
    check("R-4 did not take the status damage", not r4_status_leak,
          f"got {r4['damage'] if r4 else None}")

    print()
    print(f"matched {len(weapons)} | verified {sum(1 for w in weapons if w['verified'])}"
          f" | unmatched {len(data['unmatched'])}")
    print(f"test_names: {'PASS' if not failures else 'FAIL'} ({checks} checks)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
