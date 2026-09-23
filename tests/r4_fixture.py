"""R-4's row, read from the shipped data instead of being hardcoded.

Several suites use R-4 as their fixture because it is the one weapon whose
behaviour has been confirmed in game. The numbers that identify its row - its
position, its type id, its values, its neighbours - move with every balance
patch: 1.8.45317 had position 137 / id 137 / 220/45, and 1.8.45850 has position
147 / id 142 / 220/45. A hardcoded copy therefore silently stops describing the
row the mod writes, which makes a test read one row and the mod write another.

Reading them here means a rebalance updates every suite at once, and a test can
only fail because the code is wrong, not because its constants aged.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load() -> dict:
    wn = json.loads((ROOT / "data" / "weapon_names.json").read_text(encoding="utf-8"))
    return next(w for w in wn["weapons"] if w["page"] == "R-4 Hyena")


def r4_row() -> dict:
    """R-4's damage row as the map describes it.

    Keys: page, position, type_id, damage, durable, ap, before_id, after_id,
    next_damage, next_durable, speed, ammo.
    """
    w = _load()
    records = json.loads(
        (ROOT / "data" / "damage_records.json").read_text(encoding="utf-8"))
    by_index = {r["index"]: r for r in records}
    pos = w["damage_position"]
    before = by_index.get(pos - 1)
    after = by_index.get(pos + 1)
    return {
        "page": w["page"],
        "position": pos,
        "type_id": w["damage_index"],
        "damage": w["damage"],
        "durable": w["durable"],
        "ap": list(w["ap"]),
        "before_id": before["type_id"] if before else None,
        "after_id": after["type_id"] if after else None,
        "next_damage": after["damage"] if after else None,
        "next_durable": after["durable_damage"] if after else None,
        "speed": w.get("speed"),
        "ammo": w.get("ammo"),
    }


def lua_record(row: dict | None = None) -> str:
    """The row as a Lua table literal, for embedding in a probe script."""
    r = row or r4_row()
    ap = ", ".join(str(v) for v in r["ap"])
    return (f"{{ type_id = {r['type_id']}, position = {r['position']}, "
            f"damage = {r['damage']}, durable = {r['durable']}, "
            f"ap = {{{ap}}} }}")


def lua_expect(row: dict | None = None) -> str:
    """The row's neighbours as a Lua `expect` table literal."""
    r = row or r4_row()
    return (f"{{ before_id = {r['before_id']}, after_id = {r['after_id']}, "
            f"next = {{ damage = {r['next_damage']}, "
            f"durable = {r['next_durable']} }} }}")


if __name__ == "__main__":
    row = r4_row()
    for k, v in row.items():
        print(f"  {k}: {v}")
