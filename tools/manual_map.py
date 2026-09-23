"""Weapons whose mapping cannot be derived from the wiki, registered by hand.

WHY THIS EXISTS

A weapon reaches its damage row through a chain:

    weapon name (wiki) -> ammo name (wiki) -> projectile row (game) -> damage row

Every hop needs a key that exists on both sides. Two cases break it:

  * the wiki's machine-readable table has not been updated for a newly released
    weapon, so there is no ammo name at all (the page prose has figures, the
    table the tool reads is empty); or
  * the game gives two different weapons the SAME ammo string. GL-15 Evictor and
    GL-21 Grenade Launcher both reference string key 1639593129
    ("40mm HE Grenade"), so no name-based rule can tell their rows apart. That is
    a fact about the game data, not a limitation of the matcher.

WHAT IS REGISTERED, AND WHY IT IS SAFE

Each entry carries a VALUE FINGERPRINT taken from in-game measurement, not a row
number. This is the important choice:

  * a row number is unique but NOT stable - across the 1.8.45850 update only 7 of
    162 name-matched projectile rows kept their index (4.3%), and 152 of 162
    moved. A stored row number would silently address a different projectile
    after the next update, and nothing would detect it, because an index is
    always "valid". That is the failure mode this project exists to prevent.
  * a value fingerprint fails LOUDLY. If the game rebalances any part of it, the
    fingerprint stops matching, the row is not found, and the weapon is refused.
    A refusal is recoverable; a wrong write is not.

Every field in a fingerprint must match. Partial matches are not accepted: with
one field free, GL-15's explosion alone (440/AP3) would still single out its row,
but a rebalance of the direct hit would then go unnoticed while the weapon kept
resolving - which is exactly the drift the fingerprint is meant to catch.

WHAT THIS COSTS

The figures are hand-entered, so they are only as good as the measurement, and a
game rebalance invalidates the entry. The failure is safe (refusal), but it is
silent until someone regenerates the map and reads the note. When the wiki's
table catches up, DELETE the entry: the normal matcher will handle the weapon,
and the derived mapping is preferable to a hand measurement.

Adding an entry is a deliberate act. Do not add one on a guess - a fingerprint
that does not correspond to the weapon would map it to whatever row happens to
match, which is the same class of error as a wrong row number.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Fingerprint:
    """The values a weapon must have for its row to be accepted.

    `direct` is (damage, durable, AP) on the projectile's own damage row.
    `explosion` is (damage, AP) on the row its explosion points at, and
    `radii` is (inner, outer) on the explosion record itself.

    `None` means "not constrained" and is deliberately not used for any field
    that has been measured - see the module docstring on partial matches.
    """

    direct: tuple[int, int, int]
    explosion: tuple[int, int] | None = None
    radii: tuple[float, float] | None = None
    # Free-text: who measured it, when, and how. Shown when the entry is used,
    # and the only thing that makes a stale entry diagnosable later.
    source: str = ""


@dataclass(frozen=True)
class ManualEntry:
    page: str
    fingerprint: Fingerprint
    # The payload segment the user edits. "explosion" points damage_position at
    # the explosion's damage row and keeps the impact token separately, matching
    # what the automatic matcher does for two-part weapons.
    payload: str = "explosion"
    notes: tuple[str, ...] = field(default_factory=tuple)


MANUAL: tuple[ManualEntry, ...] = (
    ManualEntry(
        page="GL-15 Evictor",
        fingerprint=Fingerprint(
            direct=(50, 2, 3),
            explosion=(440, 3),
            radii=(2.25, 5.50),
            source=(
                "in-game measurement by the maintainer, 2026-09-23: direct hit "
                "50 damage / 2 durable / AP3, explosion 440 / AP3. Note "
                "50 + 440 = 490, which is the figure the wiki page states - so "
                "the wiki's bare 490 is the sum of the two segments."
            ),
        ),
        payload="explosion",
        notes=(
            "The wiki has no Attack Data row for this weapon yet, and the game "
            "gives it the same ammo string as GL-21 Grenade Launcher, so this "
            "cannot be derived. Remove this entry once the wiki table catches up.",
            "The fingerprint is unique in the table: the direct tuple, the "
            "explosion tuple and the radii each single out exactly one row.",
        ),
    ),
)


def by_page() -> dict[str, ManualEntry]:
    return {e.page: e for e in MANUAL}


def describe() -> list[str]:
    """One line per entry, for the build log."""
    out = []
    for e in MANUAL:
        fp = e.fingerprint
        out.append(
            f"{e.page}: direct {fp.direct}"
            + (f", explosion {fp.explosion}" if fp.explosion else "")
            + (f", radii {fp.radii[0]:g}/{fp.radii[1]:g}" if fp.radii else "")
            + f"  [{fp.source.split(':')[0]}]"
        )
    return out
