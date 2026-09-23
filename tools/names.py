"""Resolve a damage row to a human-readable label.

Status, stated plainly so the next person does not over-trust this module:

**Resolved.** The ammo/projectile name. A projectile record carries a string id
at `+8` that keys `English.json` (the game's own string table), e.g. R-4's
projectile row resolves to `"9x70mm Full Metal Jacket"`. The lookup mechanism is
verified - `wcs` entries' `name_cased` values hit `English.json` 12/12 for the
9x20mm family and match the projectile `+8` values exactly.

**Not resolved.** The *weapon* display name ("R-4 Hyena"). It is not in

* any `.dl_bin` we have (`Hyena` and `R-4` appear 0 times),
* `English.json` (that dump predates the weapon),
* filediver's `dl_type_names.txt` (enum names are redacted to
  `DamageInfoType_Value_137_Len_53`),
* filediver's asset name list (89816 names; these are *resource paths* like
  `wep_hotshot_marksman_rifle.wwise_bank`, not display names - `Hyena` is absent
  and only 42.29% of name hashes are known).

So the GUI must not depend on a weapon display name yet. Label rows by what is
actually known: the ammo type plus the identifying stats. For R-4 that reads
`9x70mm Full Metal Jacket @ 950 m/s - 220/45 AP3`, which is unambiguous (its
neighbour at 850 m/s is the shared fmj), and is honest about what is known.

Do not paper over this with a guessed name. A wrong label on a damage row is
worse than a technical one, because the user edits it.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import dlbin_tables

PROJECTILE_RECORD_SIZE = dlbin_tables.PROJECTILE_RECORD_SIZE

# Offset inside a projectile record holding the string-table key for its name.
PROJECTILE_OFF_NAME = 8


def load_strings(path: str | Path) -> dict[str, str]:
    """Load the game's string table (filediver's `English.json`)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def projectile_name(blob: bytes, row: int, strings: dict[str, str]) -> str | None:
    """Return the ammo display name for a projectile row, or None."""
    rows = dlbin_tables.parse_projectiles(blob)
    if not 0 <= row < len(rows):
        raise IndexError(f"projectile row {row} out of range (0..{len(rows) - 1})")
    # The string key is the *cased* name hash at +8; +4 holds the upper-case one.
    key = rows[row]["name_cased"]
    return strings.get(str(key))


def label(ammo: str | None, speed: float, damage: int, durable: int, ap: int) -> str:
    """Build the GUI label.

    Includes the speed because it is what distinguishes variant rows that share a
    name: R-4 and the common fmj are both "9x70mm Full Metal Jacket" and differ
    only by speed (950 vs 850) and stats.
    """
    parts = [ammo or "(unknown ammo)"]
    parts.append(f"{speed:g} m/s")
    parts.append(f"{damage}/{durable}")
    parts.append(f"AP{ap}")
    return " · ".join(parts)


if __name__ == "__main__":
    import sys

    root = Path(__file__).resolve().parent.parent
    proj = (root / "data/raw/generated_projectile_settings.dl_bin").read_bytes()
    strings = load_strings(sys.argv[1] if len(sys.argv) > 1 else
                           Path.home() / "AppData/Local/Temp/hd2mod/assessment/English.json")

    print("projectile name resolution:")
    for row in (239, 240, 245):
        print(f"  row {row}: {projectile_name(proj, row, strings)!r}")
    print()
    r4 = projectile_name(proj, 245, strings)
    print("R-4 label:", label(r4, 950.0, 220, 45, 3))
