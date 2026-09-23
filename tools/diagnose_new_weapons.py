"""Can the weapons this build added be mapped? Answer per weapon, with evidence.

The user asked to add the new weapons. Whether that is possible depends on a
chain the tool cannot skip:

    weapon name (wiki)  ->  ammo name (wiki)  ->  projectile row (game)
                        ->  damage row (game)

Each hop needs a key that exists on both sides. This script checks, for every
weapon the wiki knows but the map does not, exactly which hop fails and why -
so the answer is "this weapon is unreachable because X", not "it did not match".

Run:  python tools/diagnose_new_weapons.py
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import dlbin_tables as D  # noqa: E402

WIKI = ROOT / "data" / "wiki" / "pages"


def _strings() -> dict:
    return json.loads((ROOT / "data" / "strings.json").read_text(encoding="utf-8"))


def _en(strings: dict, h: int) -> str | None:
    if not h:
        return None
    v = strings.get(str(h))
    if isinstance(v, dict):
        return v.get("English (US)") or v.get("English (UK)")
    return v


def _page_text(title: str) -> str | None:
    p = WIKI / (title.replace(" ", "_").replace("/", "_") + ".json")
    if not p.exists():
        return None
    doc = json.loads(p.read_text(encoding="utf-8"))
    txt = html.unescape(re.sub(r"<[^>]+>", " ", doc.get("html", "")))
    return " ".join(txt.split())


def _wiki_stats(txt: str) -> dict:
    """Whatever numbers the page states, in any of the wiki's formats.

    The wiki is mid-migration: primary weapons carry an Attack Data table, while
    throwables and recently added weapons carry a flat infobox line
    ("... Damage 490 Penetration Medium ..."). Both are read, and which one
    answered is reported, because a flat infobox has no ammo name and no
    durable-damage split - so it cannot satisfy the four-field cross-check.
    """
    out: dict = {}
    m = re.search(r"Standard Damage\s+(\d+)", txt)
    if m:
        out["damage"] = int(m.group(1))
        out["source"] = "infobox"
    m = re.search(r"Armor Penetration\s+([A-Za-z /-]+?)\s+(?:Fire Rate|Ammo|Spare|Handling|Recoil|$)",
                  txt)
    if m:
        out["ap_name"] = m.group(1).strip()
        out["source"] = "infobox"
    m = re.search(r"Throwable Details\s+Damage\s+(\d+)\s+Penetration\s+([A-Za-z /-]+?)\s+"
                  r"(?:Outer Radius|Cookable|Fuse|Capacity)", txt)
    if m:
        out["damage"] = int(m.group(1))
        out["ap_name"] = m.group(2).strip()
        out["source"] = "throwable infobox"
    return out


def main() -> int:
    strings = _strings()
    prj = D.parse_projectiles(
        (ROOT / "data" / "raw" / "generated_projectile_settings.dl_bin").read_bytes())
    dmg = D.parse_damages(
        (ROOT / "data" / "raw" / "generated_damage_settings.dl_bin").read_bytes())
    by_id = {r["type_id"]: r for r in dmg}
    old = D.parse_damages(
        (ROOT / ".tools" / "old" / "generated_damage_settings.dl_bin").read_bytes())
    old_ids = {r["type_id"] for r in old}

    wn = json.loads((ROOT / "data" / "weapon_names.json").read_text(encoding="utf-8"))
    mapped = {w["page"] for w in wn["weapons"]}

    # The weapons this build added: wiki pages the map does not know.
    pages = sorted(p.stem.replace("_", " ") for p in WIKI.glob("*.json"))
    candidates = [p for p in pages if p not in mapped]

    print(f"map knows {len(mapped)} weapons; {len(candidates)} wiki pages are unmapped")
    print()

    # Which projectile rows are new in this build, and do they have names?
    new_rows = [p for p in prj if p["row"] >= 343]
    print(f"== projectile rows added by this build ({len(new_rows)}) ==")
    named = 0
    for p in new_rows:
        nm = _en(strings, p["name_cased"]) or _en(strings, p["name_upper"])
        if nm:
            named += 1
        t = by_id.get(p["damage_type"])
        dd = (f"{t['damage']}/{t['durable_damage']} AP{t['armor_penetration_per_angle'][0]}"
              if t else "no damage row")
        print(f"  row {p['row']:3d}  {nm or '(no string)':28s} -> {dd}")
    print(f"  {named} of {len(new_rows)} carry a name string; the rest cannot be "
          f"reached by name")

    print()
    print("== damage rows added by this build ==")
    for r in dmg:
        if r["type_id"] in old_ids:
            continue
        owners = [p for p in prj if p["damage_type"] == r["type_id"]]
        who = ", ".join(
            (_en(strings, p["name_cased"]) or _en(strings, p["name_upper"])
             or f"row {p['row']} (unnamed)") for p in owners) or "nothing references it"
        print(f"  pos {r['position']:3d}  id {r['type_id']:4d}  "
              f"{r['damage']:5d}/{r['durable_damage']:5d} AP{r['armor_penetration_per_angle'][0]:2d}"
              f"  <- {who}")

    print()
    print("== the unmapped wiki pages, and which hop fails ==")
    reachable = []
    for page in candidates:
        txt = _page_text(page)
        if txt is None:
            print(f"  {page}: no cached page")
            continue
        st = _wiki_stats(txt)
        nf = "Weapon not found" in txt
        kind = ("throwable" if "Throwable Details" in txt
                else "weapon" if "Weapon Category" in txt else "other")
        why = []
        if nf:
            why.append("wiki has no Attack Data row (stats only in a flat infobox)")
        if not st:
            why.append("no damage number on the page at all")
        elif "durable" not in st and kind == "weapon":
            why.append("no durable-damage split, so the 4-field check cannot pass")
        if kind == "throwable":
            why.append("a throwable: no ammo, no projectile, so no row to edit")
        print(f"  {page}  [{kind}]")
        if st:
            print(f"      wiki says: {st}")
        for w in why:
            print(f"      - {w}")
        if not why:
            reachable.append(page)

    print()
    print(f"== reachable without more information: {len(reachable)} ==")
    for p in reachable:
        print(f"  {p}")
    if not reachable:
        print("  none - every candidate is missing a key the chain needs")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
