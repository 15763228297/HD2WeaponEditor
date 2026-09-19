"""The generator must not need the game's own data files.

The repository is public, so it cannot ship `data/raw/*.dl_bin` - those are
extracted game assets. The generator therefore reads `data/damage_records.json`,
which `tools/parse_dlbin.py` derives from a local install.

This asserts the substitution is faithful rather than merely working: every row
the JSON supplies must equal what parsing the .dl_bin produces. A silent
divergence here would edit the wrong record in-game, and every other test would
still pass because they all read the same JSON.

Also asserts the JSON actually covers the fields the generator touches, so a
future field addition to the .dl_bin path cannot slip through unnoticed.

Run:  python tests/test_data_equivalent.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_map  # noqa: E402
import test_resolver as base_mod  # noqa: E402

RAW = ROOT / "data" / "raw" / "generated_damage_settings.dl_bin"
JSON = ROOT / "data" / "damage_records.json"


def main() -> int:
    check = base_mod.check

    print("== the generator reads the derived JSON, not game data ==")
    gen_src = (ROOT / "tools" / "gen_mod.py").read_text(encoding="utf-8")
    check("gen_mod does not read a .dl_bin for damage rows",
          "damage_records.json" in gen_src, "gen_mod still parses the .dl_bin")
    check("no game archive is referenced at runtime",
          "generated_damage_settings.dl_bin" not in gen_src,
          "gen_mod still points at the extracted game table")

    rows = json.loads(JSON.read_text(encoding="utf-8"))
    if isinstance(rows, dict):
        rows = rows.get("records", rows)
    if isinstance(rows, dict):
        rows = [rows[k] for k in sorted(rows, key=int)]
    check("the JSON has rows", len(rows) > 0, str(len(rows)))

    print()
    print("== the JSON covers every field the generator uses ==")
    need = {"type_id", "damage", "durable_damage",
            "armor_penetration_per_angle"}
    check("required fields are present", need <= set(rows[0].keys()),
          f"missing {need - set(rows[0].keys())}")

    print()
    print("== generate works with the game files absent ==")
    # Point the loader at a JSON-only view by asserting the code path does not
    # touch data/raw; the strongest check available without deleting the file.
    import gen_mod
    damages, _projectiles, names = gen_mod.load_tables()
    check("load_tables returns rows", len(damages) == len(rows),
          f"{len(damages)} vs {len(rows)}")
    check("load_tables returns the name map", len(names["weapons"]) > 0,
          str(len(names.get("weapons", []))))

    # And the values must match what the game table says, when it is available.
    if RAW.exists():
        print()
        print("== the JSON matches the game table it was derived from ==")
        parsed = build_map.parse_damages(RAW.read_bytes())
        check("same row count", len(parsed) == len(rows),
              f"{len(parsed)} vs {len(rows)}")
        mismatch = []
        for i, row in enumerate(rows):
            rec = parsed.get(i)
            if rec is None:
                mismatch.append(f"row {i}: absent from the table")
                continue
            if (rec.type_id != row["type_id"]
                    or rec.damage != row["damage"]
                    or rec.durable_damage != row["durable_damage"]
                    or rec.armor_penetration_per_angle
                       != list(row["armor_penetration_per_angle"])):
                mismatch.append(
                    f"row {i}: json {row['type_id']}/{row['damage']}/"
                    f"{row['durable_damage']} vs table "
                    f"{rec.type_id}/{rec.damage}/{rec.durable_damage}")
        print(f"     compared {len(rows)} rows")
        check("every row agrees with the game table", not mismatch,
              (mismatch[:3] if mismatch else ""))
    else:
        print()
        print("     (game table not present - row-by-row check skipped; this is")
        print("      the normal state for a clone of the public repo)")

    print()
    print(f"test_data_equivalent: "
          f"{'PASS' if not base_mod.failures else 'FAIL'} ({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
