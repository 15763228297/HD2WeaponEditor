"""Shared rows and the impact half of explosive weapons.

Two behaviours, both opt-in or newly visible:

  * A damage row used by several weapons can now be edited, but only when the
    caller passes allow_shared. Default stays refused, because silently
    changing other weapons is the one failure this tool exists to prevent.

  * Explosive weapons deal damage twice - the projectile hitting, then the
    explosion - as two separate damage records with separate owners. The impact
    half used to be shown only as a row number, hiding half the weapon's
    damage. It is now readable and editable.

Run:  python tests/test_shared_and_impact.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

import test_resolver as base_mod  # noqa: E402


def main() -> int:
    check = base_mod.check
    sys.path.insert(0, str(ROOT / "tools"))
    import gen_mod

    names = json.loads((ROOT / "data" / "weapon_names.json").read_text(encoding="utf-8"))
    weapons = {w["page"]: w for w in names["weapons"]}

    # A weapon whose damage row is shared.
    shared_page = None
    for page, w in weapons.items():
        if not w["exclusive"] and w["verified"]:
            shared_page = page
            break

    print("== a shared damage row is refused by default ==")
    # A dataset with no shared rows at all means the exclusivity annotation is
    # broken (it was once keyed on type_id instead of position, which reported
    # the wrong peers). Fail loudly rather than skipping the checks silently.
    check("the dataset actually contains a shared row", shared_page is not None,
          "no shared+verified weapon found - is the name map stale?")
    if shared_page is None:
        print(f"test_shared_and_impact: FAIL ({base_mod.checks} checks)")
        return 1
    print(f"     using {shared_page} (peers: "
          f"{', '.join(weapons[shared_page]['shared_with'])})")
    try:
        gen_mod.build_spec(shared_page, damage=500, durable=150, ap=6)
        check("shared row is refused without allow_shared", False,
              "it was accepted - other weapons would change silently")
    except SystemExit as exc:
        check("shared row is refused without allow_shared", True, "")
        check("the refusal names the affected weapons",
              all(p in str(exc) for p in weapons[shared_page]["shared_with"]),
              str(exc)[:120])

    print()
    print("== allow_shared opts in ==")
    spec = gen_mod.build_spec(shared_page, damage=500, durable=150, ap=6,
                              allow_shared=True)
    check("allow_shared lets it through", spec.changes.get("damage") == 500,
          str(spec.changes))

    # ---- impact half -------------------------------------------------------
    print()
    print("== the impact half of an explosive weapon ==")
    impact_exclusive = [
        p for p, w in weapons.items()
        if w.get("payload") == "explosion"
        and w.get("impact_damage_position") is not None
        and w.get("impact_exclusive")
    ]
    impact_shared = [
        p for p, w in weapons.items()
        if w.get("payload") == "explosion"
        and w.get("impact_damage_position") is not None
        and not w.get("impact_exclusive")
    ]
    print(f"     {len(impact_exclusive)} with an exclusive impact row, "
          f"{len(impact_shared)} with a shared one")
    check("some explosive weapons have an exclusive impact row",
          len(impact_exclusive) > 0, str(len(impact_exclusive)))

    page = impact_exclusive[0]
    w = weapons[page]
    try:
        ispec = gen_mod.build_impact_spec(page, damage=200, durable=150, ap=6)
        check(f"{page}: impact spec builds without allow_shared", True, "")
        check("it targets the impact position, not the explosion position",
              ispec.position == w["impact_damage_position"]
              and ispec.position != w["damage_position"],
              f"got {ispec.position}, impact={w['impact_damage_position']}, "
              f"explosion={w['damage_position']}")
        check("the baseline matches the impact row's values",
              ispec.baseline["damage"] == _row_value(w["impact_damage_position"]),
              str(ispec.baseline))
    except SystemExit as exc:
        check(f"{page}: impact spec builds without allow_shared", False, str(exc))

    print()
    print("== a shared impact row is refused by default too ==")
    if impact_shared:
        sp = impact_shared[0]
        peers = weapons[sp]["impact_shared_with"]
        print(f"     using {sp} (peers: {', '.join(peers)})")
        try:
            gen_mod.build_impact_spec(sp, damage=200, durable=150, ap=6)
            check("shared impact row is refused by default", False, "it was accepted")
        except SystemExit as exc:
            check("shared impact row is refused by default", True, "")
            check("the refusal names the affected weapons",
                  all(p in str(exc) for p in peers), str(exc)[:120])
        allowed = gen_mod.build_impact_spec(sp, damage=200, durable=150, ap=6,
                                            allow_shared=True)
        check("allow_shared lets the shared impact row through",
              allowed.changes.get("damage") == 200, str(allowed.changes))
    else:
        print("     none found - skipped")

    print()
    print("== a weapon with no impact row says so instead of guessing ==")
    no_impact = [p for p, w in weapons.items()
                 if w.get("payload") == "explosion"
                 and w.get("impact_damage_position") is None]
    check("there are pure-explosion weapons", len(no_impact) > 0, str(no_impact))
    if no_impact:
        try:
            gen_mod.build_impact_spec(no_impact[0], damage=1, durable=1, ap=1)
            check("no-impact weapon is refused", False, "it was accepted")
        except SystemExit as exc:
            check("no-impact weapon is refused", True, "")
            # The message is localised, so match on either language.
            check("the message explains why",
                  ("impact" in str(exc).lower() or "直击" in str(exc)),
                  str(exc)[:100])

    print()
    print(f"test_shared_and_impact: "
          f"{'PASS' if not base_mod.failures else 'FAIL'} ({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


def _row_value(position: int):
    """Read a damage row's damage value straight from the parsed table."""
    rows = json.loads((ROOT / "data" / "damage_records.json").read_text(encoding="utf-8"))
    if isinstance(rows, dict):
        rows = [rows[k] for k in sorted(rows, key=int)]
    return rows[position]["damage"]


if __name__ == "__main__":
    raise SystemExit(main())
