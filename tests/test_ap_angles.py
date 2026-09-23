"""The three penetration angles are independent values, and must stay so.

The game stores four penetration tiers per damage row - direct, slight angle,
large angle, extreme angle - and the first three genuinely differ on most rows:
346 of 649 have them disagree, most commonly (3, 0, 0) rather than (3, 3, 3).

The tool used to accept only one tier and copy it over the first three, refusing
when the user made them differ. That was a guess made before the data was
examined. These tests hold the new behaviour:

  * three different tiers produce three different writes
  * the fourth angle is never written, because 0 there means "Unarmored"
    rather than a tier - 568 of 649 rows hold 0
  * the single-tier form still works, for the CLI and older callers
  * a value outside 0..10 is refused on every angle, not just the first
  * the API accepts both request shapes and validates each

Run:  python tests/test_ap_angles.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_mod  # noqa: E402

PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f"  -- {detail}" if detail else ""))


# A weapon whose row is exclusive, so no shared-record confirmation is involved.
WEAPON = "R-4 Hyena"


def main() -> int:
    # -- the data really is per-angle ----------------------------------
    from build_map import parse_damages
    damages = parse_damages(
        (ROOT / "data/raw/generated_damage_settings.dl_bin").read_bytes())
    differ = sum(1 for r in damages.values()
                 if not (r.armor_penetration_per_angle[0]
                         == r.armor_penetration_per_angle[1]
                         == r.armor_penetration_per_angle[2]))
    check("the game's rows really do differ per angle",
          differ > 100, f"{differ} of {len(damages)} rows differ")
    fourth_zero = sum(1 for r in damages.values()
                      if r.armor_penetration_per_angle[3] == 0)
    check("the fourth angle is 0 on most rows (so it is a sentinel, not a tier)",
          fourth_zero > len(damages) * 0.8,
          f"{fourth_zero} of {len(damages)}")

    # -- three different tiers are written as three different values -----
    spec = gen_mod.build_spec(WEAPON, damage=400, durable=200,
                              ap_angles=[9, 6, 1])
    check("ap0 takes the first tier", spec.changes.get("ap0") == 9,
          str(spec.changes))
    check("ap1 takes the second tier", spec.changes.get("ap1") == 6,
          str(spec.changes))
    check("ap2 takes the third tier", spec.changes.get("ap2") == 1,
          str(spec.changes))
    check("the three tiers are not collapsed to one",
          len({spec.changes.get("ap0"), spec.changes.get("ap1"),
               spec.changes.get("ap2")}) == 3, str(spec.changes))

    # -- the fourth angle is never written ------------------------------
    check("ap3 is not among the changes", "ap3" not in spec.changes,
          str(spec.changes))
    check("the baseline still records ap3 so the runtime can restore it",
          "ap3" in spec.baseline, str(spec.baseline))

    # -- (3, 0, 0) is expressible ---------------------------------------
    # This is the most common shape in the data (114 rows) and the one the old
    # code refused outright.
    spec2 = gen_mod.build_spec(WEAPON, damage=400, durable=200,
                               ap_angles=[3, 0, 0])
    check("(3, 0, 0) is accepted", spec2.changes.get("ap1") == 0
          and spec2.changes.get("ap2") == 0, str(spec2.changes))
    check("(3, 0, 0) keeps ap0 at 3", spec2.changes.get("ap0") is None
          or spec2.changes.get("ap0") == 3, str(spec2.changes))

    # -- the single-tier shorthand still works --------------------------
    spec3 = gen_mod.build_spec(WEAPON, damage=400, durable=200, ap=7)
    check("a single tier applies to all three angles",
          spec3.changes.get("ap0") == 7 and spec3.changes.get("ap1") == 7
          and spec3.changes.get("ap2") == 7, str(spec3.changes))
    check("a single tier still does not touch ap3",
          "ap3" not in spec3.changes, str(spec3.changes))

    # -- validation covers every angle, not just the first ---------------
    for bad in ([11, 0, 0], [0, 11, 0], [0, 0, 11], [-1, 0, 0]):
        try:
            gen_mod.build_spec(WEAPON, damage=400, durable=200, ap_angles=bad)
            check(f"out-of-range {bad} is refused", False, "accepted")
        except SystemExit:
            check(f"out-of-range {bad} is refused", True)

    try:
        gen_mod.build_spec(WEAPON, damage=400, durable=200, ap_angles=[1, 2])
        check("a wrong-length angle list is refused", False, "accepted")
    except SystemExit:
        check("a wrong-length angle list is refused", True)

    # -- the CLI accepts both forms -------------------------------------
    edits = gen_mod.parse_edits([f"{WEAPON}::400/200/7"], None, None, None)
    check("CLI: the 3-value form gives three equal tiers",
          edits[0][3] == [7, 7, 7], str(edits[0]))
    edits = gen_mod.parse_edits([f"{WEAPON}::400/200/9/6/1"], None, None, None)
    check("CLI: the 5-value form gives three separate tiers",
          edits[0][3] == [9, 6, 1], str(edits[0]))
    check("CLI: damage and durable still parse from the 5-value form",
          (edits[0][1], edits[0][2]) == (400, 200), str(edits[0]))
    try:
        gen_mod.parse_edits([f"{WEAPON}::400/200/1/2/3/4"], None, None, None)
        check("CLI: a 6-value form is refused", False, "accepted")
    except SystemExit:
        check("CLI: a 6-value form is refused", True)

    # parse_edits raises SystemExit for a bad spec, which escapes as an exception
    # rather than a failed check. Call it through a wrapper so a rejection is
    # reported as a check result - a test that dies on the way to its own
    # assertion reports nothing, and the verifier that reads only FAIL lines
    # counts it as "not caught".
    def rejects(spec: str) -> bool:
        try:
            gen_mod.parse_edits([spec], None, None, None)
            return False
        except SystemExit:
            return True

    check("CLI: the 5-value form is accepted",
          not rejects(f"{WEAPON}::400/200/9/6/1"))
    check("CLI: a 4-value form is refused", rejects(f"{WEAPON}::400/200/9/6"))
    check("CLI: a 6-value form is refused by the wrapper too",
          rejects(f"{WEAPON}::400/200/9/6/1/2"))

    # -- end to end through build_specs ---------------------------------
    specs = gen_mod.build_specs([f"{WEAPON}::400/200/9/6/1"])
    check("build_specs carries the three tiers through",
          specs[0].changes.get("ap0") == 9
          and specs[0].changes.get("ap1") == 6
          and specs[0].changes.get("ap2") == 1, str(specs[0].changes))

    # -- the generated Lua carries them separately ----------------------
    sources = {name: (ROOT / "mod_template" / "src" / name).read_text(encoding="utf-8")
               for name in gen_mod.MODULES}
    lua = gen_mod.render_module(specs, sources)
    check("the generated Lua has ap0 = 9", "ap0 = 9" in lua)
    check("the generated Lua has ap1 = 6", "ap1 = 6" in lua)
    check("the generated Lua has ap2 = 1", "ap2 = 1" in lua)
    check("the generated Lua keeps ap3 in the baseline",
          "ap3 = 0" in lua, "ap3 missing from baseline")

    # -- the GUI no longer refuses differing angles ---------------------
    html = (ROOT / "gui/templates/index.html").read_text(encoding="utf-8")
    check("the GUI no longer refuses differing angles",
          "生成器只接受一个穿甲等级" not in html)
    check("the GUI still rejects non-numeric input",
          "数值必须是整数" in html)
    # The single-weapon generate() must put the three tiers in its request body.
    # Checking for the bare token `angles` was not enough: the queue path spreads
    # `...e`, which contains `angles` too, so removing the field from the body
    # left the assertion passing.
    gen_body = html[html.index("const body = {"):]
    gen_body = gen_body[:gen_body.index("};")]
    check("the generate request body carries the three tiers",
          "angles," in gen_body, gen_body.replace("\n", " ")[:200])
    check("the generate request body still carries ap for compatibility",
          "damage, durable, ap," in gen_body, gen_body[:200])
    # And the values come from the panel's own three inputs, not from one field
    # copied three times.
    check("the tiers are read from three separate inputs",
          'const angles = [num("ap0"), num("ap1"), num("ap2")];' in html)
    check("currentEdit sends the angles it read",
          "ap: angles[0], angles," in html)

    # -- the API accepts and validates both shapes ----------------------
    sys.path.insert(0, str(ROOT / "gui"))
    import app as gui_app
    check("API: three tiers pass through",
          gui_app._angles_of({"angles": [9, 6, 1]}) == [9, 6, 1])
    check("API: a single ap expands to three",
          gui_app._angles_of({"ap": 5}) == [5, 5, 5])
    check("API: neither shape gives None",
          gui_app._angles_of({}) is None)
    check("API: a wrong-length list falls back to ap when present",
          gui_app._angles_of({"angles": [1, 2], "ap": 4}) == [4, 4, 4])

    print(f"\n{PASS} PASS, {FAIL} FAIL")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
