"""Match damage records to weapon names by scraping the wiki.

Why the wiki instead of the game files: the weapon display name is not in any
data table we can read - `Hyena` appears in the game's `.strings` resources but
nothing in `generated_*.dl_bin` references that key. Rather than guess, take the
name from the wiki, which documents the ammo type per weapon in a structured,
machine-readable way.

What the wiki gives us (verified on R-4 Hyena):
    * 9x70mm FULL METAL JACKET P1   <- the ammo variant, matches projectile +8
    Initial Velocity  950 m/s       <- matches the projectile record
    Standard 220 / vs. Durable 45   <- matches the damage record
    Penetration Medium (AP3)        <- matches armor_penetration_per_angle[0]

So the wiki's ammo string + velocity is a **cross-check against our parsed
records**, not just a label source: if the two disagree, the parse or the wiki is
stale and we must not ship a mapping built on it.

Usage:
    python tools/wiki_names.py fetch     # download rendered pages (cached)
    python tools/wiki_names.py build     # parse + join + write data/weapon_names.json
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "wiki" / "pages"
API = "https://helldivers.wiki.gg/api.php"

# Pages in Category:Weapons that are not weapons.
SKIP = {"Damage Comparison", "Weapons", "Weapons/zh-tw", "Weapon Customization"}


def _get(url: str) -> str:
    # The wiki rate-limits clients without a descriptive User-Agent and answers
    # with an `error: ratelimited` JSON body, not an HTTP error - so a naive
    # fetch silently returns "no text" for every page after the first few.
    # Send a UA and back off on the explicit error code.
    for attempt in range(5):
        p = subprocess.run(
            [
                "curl", "-sL", "--ssl-no-revoke", "--max-time", "90",
                "-H", "User-Agent: HD2WeaponEditor/0.1 (local research tool)",
                url,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        body = p.stdout or ""
        if '"code":"ratelimited"' in body:
            wait = 5 + attempt * 5
            print(f"    rate limited, waiting {wait}s")
            time.sleep(wait)
            continue
        if body.strip():
            return body
        time.sleep(2 + attempt * 2)
    raise RuntimeError(f"empty or rate-limited response from {url}")


def fetch() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    pages = [
        t.strip()
        for t in (ROOT / "data" / "wiki" / "weapon_pages.txt").read_text(encoding="utf-8").splitlines()
        if t.strip() and t.strip() not in SKIP
    ]
    print(f"{len(pages)} pages")
    for i, title in enumerate(pages, 1):
        out = CACHE / (title.replace("/", "_").replace(" ", "_") + ".json")
        if out.exists() and out.stat().st_size > 1000:
            continue
        url = f"{API}?action=parse&page={title.replace(' ', '_')}&prop=text&format=json&redirects=1"
        try:
            raw = _get(url)
            data = json.loads(raw)
        except Exception as e:
            print(f"  [{i}] FAIL {title}: {e}")
            continue
        html = (data.get("parse") or {}).get("text", {}).get("*")
        if not html:
            print(f"  [{i}] no text {title}")
            continue
        out.write_text(json.dumps({"title": title, "html": html}), encoding="utf-8")
        if i % 10 == 0:
            print(f"  [{i}/{len(pages)}] {title}")
        time.sleep(1.2)  # be polite; the wiki rate-limits aggressive clients
    print("fetch done")


# Anchor the ammo name on the attack table, then read the clean display string
# that sits immediately before it:
#   <span class="remote-text">5.5x50mm FULL METAL JACKET P</span> ... <table
#     class="... attack-data-table-projectile" id="55x50mm&#95;FULL&#95;...">
# The table's `id` is HTML-attribute-mangled (`&#95;` for `_`, and `5.5x50mm`
# collapses to `55x50mm`), so it is only good as a *locator*. The `remote-text`
# span is the display form and is what must be compared.
#
# An earlier version took the first `remote-trigger` span on the page; on many
# pages that is the *weapon* name ('AR-2', 'B/FLAM-80 CREMATOR_S_DM'), which
# produced garbage ammo strings and silently matched nothing.
# Anchor the ammo name on the PROJECTILE attack table specifically. A weapon page
# can carry several attack tables (R-4 has `attack-data-table-projectile` and
# `attack-data-table-status`), and taking the first/last span indiscriminately
# picked the status row's label ("Fire") as the ammo name. The projectile table
# is the one whose stats this tool edits.
AMMO_TABLE_RE = re.compile(r'attack-data-table-projectile"\s+id="[^"]*"')
EXPLOSION_TABLE_RE = re.compile(r'attack-data-table-explosion"\s+id="[^"]*"')
ARC_TABLE_RE = re.compile(r'attack-data-table-arc"\s+id="[^"]*"')
STATUS_TABLE_RE = re.compile(r'attack-data-table-status"\s+id="[^"]*"')
# Any attack table, capturing its kind - used to check which one comes first.
ATTACK_TABLE_ANY_RE = re.compile(r'attack-data-table-([a-z_]+)"\s+id="[^"]*"')
REMOTE_TEXT_RE = re.compile(r'<span class="remote-text">([^<]+)</span>')
RADIUS_RE = re.compile(r"([\d\.]+)\s*m")
EXPL_DMG_RE = re.compile(r'">(\d+)\s+(?:Explosion|Fire|Gas)')
# Row label / value pairs inside the stats tables.
ROW_RE = re.compile(r"<td>\s*([A-Za-z][^<]{0,40}?)\s*</td>\s*<td>(.*?)</td>", re.S)
VEL_RE = re.compile(r"([\d,\.]+)\s*m/s")
# Damage cells render as "220 Ballistic", "100 Damage", "60 Gas", "50 Fire".
# Accepting only "Ballistic" silently dropped any weapon whose element reads
# differently (MGX-42's page says "100 Damage"; every Gas weapon says "NN Gas"),
# which then showed up as a mismatch with standard=None.
DMG_RE = re.compile(r">\s*(\d+)\s+(Ballistic|Damage|Explosion|Fire|Gas|Arc|Plasma)\s*<")
# Armor-penetration labels are taken from the wiki's own infobox text rather than
# a hardcoded table. A hardcoded table was wrong: the wiki's icon number equals
# the game's `armor_penetration_per_angle` value directly (R-4 is AP3/Medium in
# both, the Liberator AP2/Light), and the visible strings are
#   AP2 Light, AP3 Medium, AP4 Heavy, AP5 Anti-Tank I, AP6 Anti-Tank II
# - not the off-by-one "1=Light" scheme one would assume. Reading the page's own
# text keeps the label correct even if the wiki renames a tier.
AP_CELL_RE = re.compile(r"Armor_AP(\d)_Icon\.png")
AP_TEXT_RE = re.compile(
    r'data-druid-section-row="Stats".*?druid-data-penetration[^>]*>(.*?)</div>', re.S
)


def _html_entity(s: str) -> str:
    """Decode the handful of entities the wiki emits in these fields."""
    return (
        s.replace("&#95;", "_")
        .replace("&#160;", " ")
        .replace("&amp;", "&")
        .replace("&#8226;", "*")
        .strip()
    )


def _cells(html: str) -> dict[str, str]:
    """Map row label -> raw value HTML for the stats tables."""
    out = {}
    for label, value in ROW_RE.findall(html):
        out.setdefault(label.strip(), value)
    return out


def parse_page(html: str) -> dict | None:
    """Extract ammo name and stats from a weapon page's rendered HTML.

    Uses the attack-table anchor rather than free text, so it is immune to the
    table-of-contents duplication and to the tag-stripping artefacts that broke
    an earlier text-based version.
    """
    # The ammo name is the remote-text span that belongs to the projectile table.
    # It sits in the *attacks list* before the table, but a page may also carry a
    # status table whose label ("Fire") appears between the ammo row and the
    # projectile table - R-4 Hyena does exactly that, and taking the "last span
    # before the projectile table" picked "Fire" and knocked R-4 out of the map.
    #
    # The reliable anchor is the attacks *list*: entries look like
    #   <td>*<span class="remote-trigger" data-target="...">...</span></td><td>Projectile</td>
    #   <td>**<span ...>Fire</span></td><td>status</td>
    # so pair each span with the cell that follows it and keep the one typed
    # "Projectile".
    tbl = AMMO_TABLE_RE.search(html)
    ammo = None
    for m in re.finditer(
        r'<span class="remote-text">([^<]+)</span></span>\s*</td>\s*<td>\s*([A-Za-z]+)',
        html[: tbl.start()] if tbl else html,
    ):
        if m.group(2).strip().lower() == "projectile":
            ammo = m.group(1)
    if ammo is None:
        # Fallback: the projectile table's own header cell, which repeats the
        # ammo name (e.g. <th colspan="2">9x70mm FULL METAL JACKET P1</th>).
        if tbl:
            th = re.search(r"<th[^>]*>\s*([^<]{3,60}?)\s*</th>", html[tbl.start() : tbl.start() + 800])
            if th:
                ammo = th.group(1)
        if ammo is None:
            spans = list(REMOTE_TEXT_RE.finditer(html[: tbl.start()] if tbl else html))
            if not spans:
                return None
            ammo = spans[-1].group(1)
    out: dict = {"ammo_raw": _html_entity(ammo)}

    # Everything below scopes to the projectile attack table AND STOPS at the
    # next table. A page can carry several sections with independent values:
    # R-4 Hyena has its projectile table followed by a status table whose "Fire"
    # damage (100/100) was being read as the weapon's own 220/45; TM-1 Lure Mine
    # has an explosion section at AP5 plus a shrapnel section at AP3. Without an
    # upper bound the search runs into the next section and pairs one section's
    # numbers with another's.
    if not tbl:
        return None
    nxt = ATTACK_TABLE_ANY_RE.search(html, tbl.end())
    scope = html[tbl.start() : nxt.start() if nxt else len(html)]

    for label, value in ROW_RE.findall(scope):
        low = label.strip().lower()
        if low == "initial velocity":
            v = VEL_RE.search(value)
            if v:
                out["velocity"] = float(v.group(1).replace(",", ""))
        elif low == "standard":
            d = DMG_RE.search(value)
            if d:
                out["standard"] = int(d.group(1))
        elif low == "vs. durable":
            d = DMG_RE.search(value)
            if d:
                out["durable"] = int(d.group(1))

    # Armor penetration is section-specific and must be read alongside the stats
    # it belongs to. R-36 Eruptor is the clear case: its projectile section is
    # AP4 while its explosion section (which carries the 225 Explosion payload)
    # is AP3. Reading one AP for the whole page pairs AP4 with explosion damage
    # and reports a mismatch that looks like stale data but is a scoping bug.
    out["ap_direct"] = _ap_from(scope)
    out["ap_name"] = _ap_label(scope)
    return out


def _ap_from(scope: str) -> int | None:
    m = AP_CELL_RE.search(scope)
    return int(m.group(1)) if m else None


def _ap_label(scope: str) -> str | None:
    m = AP_TEXT_RE.search(scope)
    if not m:
        return None
    txt = re.sub(r"<[^>]+>", " ", m.group(1))
    txt = re.sub(r"&[a-zA-Z#0-9]+;", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt.split("(")[0].strip() or None


def parse_arc_section(html: str) -> dict | None:
    """Parse an `attack-data-table-arc` section (electric weapons).

    Arc weapons (ARC-3, ARC-12) have no projectile table at all - they carry an
    `arc` table plus a `status` table. Without this they read as "no ammo parsed"
    and drop out of the mapping entirely.
    """
    m = ARC_TABLE_RE.search(html)
    if not m:
        return None
    seg = html[m.start() : m.start() + 6000]
    out: dict = {}

    # Damage appears as "NN Element" inside a value cell.
    dm = re.search(r'">(\d+)\s+(Arc|Fire|Explosion|Gas|Ballistic)\s*<', seg)
    if dm:
        out["damage"] = int(dm.group(1))
        out["element"] = dm.group(2)
    cells = _cells(seg)
    for label, value in cells.items():
        low = label.lower()
        if low == "vs. durable" or low == "durable":
            d = re.search(r'">(\d+)\s+\w+\s*<', value)
            if d:
                out["durable"] = int(d.group(1))
        elif low == "range":
            r = RADIUS_RE.search(value)
            if r:
                out["range"] = float(r.group(1))
    out["ap_direct"] = _ap_from(seg)
    out["ap_name"] = _ap_label(seg)
    # The arc table's own label is the ammo/attack name shown on the page.
    spans = [s.group(1) for s in REMOTE_TEXT_RE.finditer(seg[:400])]
    return out or None


def parse_explosion_only(html: str) -> dict | None:
    """Handle pages whose ONLY attack table is an explosion (thrown grenades).

    G-16 Impact is the model: one `attack-data-table-explosion`, no projectile.

    Guard hard against pages that have a projectile table or a status table:
    R-4 Hyena carries `attack-data-table-projectile` + `attack-data-table-status`,
    and an earlier version of this fallback fired anyway, picked the *status*
    span ("Fire") as its ammo name, and knocked R-4 out of the map. A page with
    any other attack table must be handled by that table's parser, never here.
    """
    if AMMO_TABLE_RE.search(html) or ARC_TABLE_RE.search(html) or STATUS_TABLE_RE.search(html):
        return None
    m = EXPLOSION_TABLE_RE.search(html)
    if not m:
        return None
    # The explosion must be the first *damage-bearing* table. A `weapon` table
    # (stat block: capacity, ergonomics, fire rate) legitimately precedes it on
    # thrown grenades like G-12 High Explosive and TED-63 Dynamite; those pages
    # have no projectile table at all. Anything else first means a different
    # parser should own this page.
    for first in ATTACK_TABLE_ANY_RE.finditer(html):
        kind = first.group(1)
        if kind == "weapon":
            continue
        if kind != "explosion":
            return None
        break
    else:
        return None
    exp = parse_explosion_section(html)
    if not exp:
        return None
    spans = list(REMOTE_TEXT_RE.finditer(html[: m.start()]))
    exp["ammo_raw"] = _html_entity(spans[-1].group(1)) if spans else "explosion"
    return exp


def parse_explosion_section(html: str) -> dict | None:
    """Parse the `attack-data-table-explosion` section of a weapon page.

    Grenades and launchers deal their real damage here, not in the projectile
    table. Verified against GL-21: the page reports Inner Radius 3.5 / Outer 7.5
    / Shockwave 8 with 400 Explosion damage at AP3, and the game's explosion
    record for that weapon carries exactly those numbers.

    An earlier version of this tool ignored this section entirely, which is why
    ~20 grenade/launcher weapons showed up as unmatched.
    """
    m = EXPLOSION_TABLE_RE.search(html)
    if not m:
        return None
    seg = html[m.start() : m.start() + 6000]
    out: dict = {}
    cells = _cells(seg)
    for label, value in cells.items():
        low = label.lower()
        if low == "inner radius":
            v = RADIUS_RE.search(value)
            if v:
                out["inner_radius"] = float(v.group(1))
        elif low == "outer radius":
            v = RADIUS_RE.search(value)
            if v:
                out["outer_radius"] = float(v.group(1))
        elif low == "shockwave radius":
            v = RADIUS_RE.search(value)
            if v:
                out["shockwave_radius"] = float(v.group(1))

    # Explosion damage is the "Inner Radius" row under the Damage heading; the
    # inner-radius *distance* row has the same label, so take the value whose
    # cell carries a damage figure rather than a distance.
    for label, value in re.findall(r"<td>\s*(Inner Radius)\s*</td>\s*<td>(.*?)</td>", seg, re.S):
        dm = EXPL_DMG_RE.search(value)
        if dm:
            out["explosion_damage"] = int(dm.group(0).split(">")[1].split()[0])
        if "Explosion" in value or "Fire" in value or "Gas" in value:
            el = re.search(r'">([A-Za-z]+) <', value)
    ap = AP_CELL_RE.search(seg)
    if ap:
        out["ap_direct"] = int(ap.group(1))
        out["ap_name"] = _ap_label(seg)
    return out or None


def _norm_ammo(s: str) -> str:
    """Normalise an ammo string for joining.

    Wiki forms seen: '9x70mm FULL METAL JACKET P1', '5.5x50mm FULL METAL JACKET P',
    '20mm APHET ROUNDS P'. Game form: '9x70mm Full Metal Jacket'.

    So strip a trailing variant marker, which is either `P<digits>` or a bare
    `P` (the page shows 'P' when all variants share one row). Case-fold as well,
    since the wiki upper-cases and the game string table does not.
    """
    s = s.strip()
    s = re.sub(r"\s+P\d*\s*$", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip().lower()


def build() -> None:
    sys.path.insert(0, str(ROOT / "tools"))
    from build_map import (parse_projectiles, parse_damages, assert_exclusive,
                           position_of_type_id)
    from names import projectile_name

    strings = json.loads((ROOT / "data" / "strings.json").read_text(encoding="utf-8"))
    eng = {
        k: (v.get("English (US)") or v.get("English (UK)") or next(iter(v.values())))
        for k, v in strings.items()
    }

    dblob = (ROOT / "data/raw/generated_damage_settings.dl_bin").read_bytes()
    pblob = (ROOT / "data/raw/generated_projectile_settings.dl_bin").read_bytes()
    damages = parse_damages(dblob)
    projectiles = parse_projectiles(pblob)

    # index projectile rows by normalised ammo name + velocity
    by_ammo: dict[str, list] = {}
    for p in projectiles:
        nm = projectile_name(pblob, p.row, eng)
        if nm:
            by_ammo.setdefault(_norm_ammo(nm), []).append(p)

    rows = []
    unmatched = []
    for f in sorted(CACHE.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        title = data["title"]
        parsed = parse_page(data["html"])
        if not parsed or "ammo_raw" not in parsed:
            # Two other page shapes carry damage but no projectile table:
            #   * arc weapons (ARC-3, ARC-12) - an `arc` table instead
            #   * thrown grenades (G-16) - an `explosion` table only
            # Both used to fall through as "no ammo parsed" and vanish from the
            # map, which is why grenades and arc weapons were missing.
            arc = parse_arc_section(data["html"])
            exp_only = parse_explosion_only(data["html"])
            if arc and arc.get("damage") is not None:
                parsed = {
                    "ammo_raw": "(arc)",
                    "standard": arc["damage"],
                    "durable": arc.get("durable"),
                    "ap_direct": arc.get("ap_direct"),
                    "explosion": None,
                    "arc": arc,
                }
            elif exp_only and exp_only.get("explosion_damage") is not None:
                parsed = {
                    "ammo_raw": exp_only.get("ammo_raw", "explosion"),
                    "explosion": exp_only,
                }
            else:
                unmatched.append((title, "no ammo parsed"))
                continue
        if "explosion" not in parsed:
            parsed["explosion"] = parse_explosion_section(data["html"])
        key = _norm_ammo(parsed["ammo_raw"])
        cands = by_ammo.get(key, [])

        # Match on the FULL stat fingerprint (velocity + damage + durable + AP),
        # not on name+velocity.
        #
        # Name+velocity alone is not unique, and not just in theory: the game
        # ships five separate '12x25mm Full Metal Jacket' rows at the same 285 m/s
        # with three different stat sets (110/22 AP2, 140/32 AP3, 130/30 AP2),
        # plus two '8x60mm High Velocity' rows at 920 m/s with 90/9 AP2 and
        # 80/18 AP3. Picking the first velocity match therefore attached the wrong
        # record to two weapons and looked "matched" while being wrong - the worst
        # failure mode for this tool, since the GUI would have offered to edit
        # another weapon's numbers.
        #
        # The fingerprint is the wiki's own numbers, so agreement across four
        # fields is strong evidence the row is the right one; a row that matches
        # on name+velocity but not on stats is a different variant and is skipped.
        def fingerprint_ok(p) -> bool:
            if parsed.get("velocity") is None or abs(p.speed - parsed["velocity"]) >= 0.5:
                return False
            d = damages.get(p.damage_position)
            if d is None:
                return False
            if parsed.get("standard") is not None and d.damage != parsed["standard"]:
                return False
            if parsed.get("durable") is not None and d.durable_damage != parsed["durable"]:
                return False
            if parsed.get("ap_direct") is not None and d.armor_penetration_per_angle[0] != parsed["ap_direct"]:
                return False
            return True

        match, how = None, None
        # Explosion-only page (no projectile table at all): thrown grenades like
        # G-12 High Explosive and TED-63 Dynamite. There is no projectile to key
        # on and the wiki label ("G-12 HIGH EXPLOSIVE E") is not a game string, so
        # match the explosion record directly on damage + radii.
        if parsed.get("explosion") and "standard" not in parsed and parsed["explosion"].get("explosion_damage") is not None:
            from explosions import parse_explosions

            exp0 = parsed["explosion"]
            eblob = (ROOT / "data/raw/generated_explosion_settings.dl_bin").read_bytes()
            for k, e in parse_explosions(eblob).items():
                if exp0.get("inner_radius") is not None and abs(e.inner_radius - exp0["inner_radius"]) > 0.01:
                    continue
                if exp0.get("outer_radius") is not None and abs(e.outer_radius - exp0["outer_radius"]) > 0.01:
                    continue
                if exp0.get("shockwave_radius") is not None and abs(e.stagger_radius - exp0["shockwave_radius"]) > 0.01:
                    continue
                if exp0.get("ap_direct") is not None and position_of_type_id(damages, e.damage_index) is not None:
                    if damages[position_of_type_id(damages, e.damage_index)].armor_penetration_per_angle[0] != exp0["ap_direct"]:
                        continue
                ed = damages.get(position_of_type_id(damages, e.damage_index))
                if ed is None or ed.damage != exp0["explosion_damage"]:
                    continue
                match = type("A", (), {"row": -1, "damage_position": position_of_type_id(damages, e.damage_index), "speed": 0.0})()
                how = "explosion-only"
                break
        # R-36 Eruptor or GL-21 deals its real damage in the explosion while the
        # projectile row is a small impact token (GL-21: 20/2). If the projectile
        # fingerprint happens to match first, the tool would offer to edit the
        # token instead of the payload.
        exp = parsed.get("explosion")
        if exp and (exp.get("inner_radius") is not None or exp.get("explosion_damage") is not None):
            from explosions import projectile_explosion, parse_explosions

            eblob = (ROOT / "data/raw/generated_explosion_settings.dl_bin").read_bytes()
            table = parse_explosions(eblob)
            for p in projectiles:
                et = projectile_explosion(pblob, p.row)
                if et is None or et not in table:
                    continue
                e = table[et]
                if exp.get("inner_radius") is not None and abs(e.inner_radius - exp["inner_radius"]) > 0.01:
                    continue
                if exp.get("outer_radius") is not None and abs(e.outer_radius - exp["outer_radius"]) > 0.01:
                    continue
                if exp.get("shockwave_radius") is not None and abs(e.stagger_radius - exp["shockwave_radius"]) > 0.01:
                    continue
                ed = damages.get(position_of_type_id(damages, e.damage_index))
                if ed is None:
                    continue
                if exp.get("explosion_damage") is not None and ed.damage != exp["explosion_damage"]:
                    continue
                if exp.get("ap_direct") is not None and ed.armor_penetration_per_angle[0] != exp["ap_direct"]:
                    continue
                match, how = p, "explosion"
                break

        if match is None:
            for p in cands:
                if fingerprint_ok(p):
                    match, how = p, "name+stats"
                    break
        if match is None:
            # The game record may carry no ammo name at all (BR-14's row 232),
            # so fall back to the fingerprint over every projectile. This is not
            # a weaker check - it is the same four-field test, just without the
            # name constraint.
            for p in projectiles:
                if fingerprint_ok(p):
                    match, how = p, "stats-only"
                    break
        if match is None and parsed.get("arc"):
            # Arc weapons: match the arc table's damage/durable/AP against a
            # damage row directly (there is no projectile to key on).
            a = parsed["arc"]
            for i, d in damages.items():
                if a.get("damage") is not None and d.damage != a["damage"]:
                    continue
                if a.get("durable") is not None and d.durable_damage != a["durable"]:
                    continue
                if a.get("ap_direct") is not None and d.armor_penetration_per_angle[0] != a["ap_direct"]:
                    continue
                match = type("A", (), {"row": -1, "damage_position": i, "speed": 0.0})()
                how = "arc"
                break

        if match is None:
            unmatched.append(
                (title, f"no projectile matches name={key!r} v={parsed.get('velocity')} "
                        f"{parsed.get('standard')}/{parsed.get('durable')} AP{parsed.get('ap_direct')}")
            )
            continue
        d = damages.get(match.damage_position)
        if d is None:
            unmatched.append((title, f"damage position {match.damage_position} missing"))
            continue

        # `match_position` is the array index used for addressing; `match_index`
        # is the row's type_id, used for identity. They are different numbers on
        # 519 of 634 rows, so both are tracked explicitly everywhere below.
        match_position = match.damage_position
        match_index = d.type_id

        # For an explosion-matched weapon, the projectile's own row is only the
        # impact token (GL-21: 20/2). The numbers a user wants to change live in
        # the explosion's damage row, so point `damage_index` at that and keep the
        # impact row separately. The GUI must edit the payload, not the token.
        exp = parsed.get("explosion") or {}
        payload = "projectile"
        impact_position = None
        impact_type_id = None
        if how == "explosion-only":
            # Already pointing at the explosion's payload row.
            payload = "explosion"
        elif how == "explosion":
            from explosions import projectile_explosion, parse_explosions

            eblob = (ROOT / "data/raw/generated_explosion_settings.dl_bin").read_bytes()
            et = projectile_explosion(pblob, match.row)
            table = parse_explosions(eblob)
            if et in table:
                # The explosion table references damage rows by TYPE ID (three
                # of its values are 635/636/639, impossible as positions in a
                # 634-row array). Convert to a position before indexing.
                et_id = table[et].damage_index
                et_pos = position_of_type_id(damages, et_id)
                ed = damages.get(et_pos) if et_pos is not None else None
                if ed is not None:
                    d_impact = d
                    d = ed
                    payload = "explosion"
                    impact_position = match_position
                    impact_type_id = d_impact.type_id
                    match_position = et_pos
                    match_index = ed.type_id
                else:
                    unmatched.append(
                        (title, f"explosion damage type {et_id} has no row"))
                    continue
        if payload == "projectile":
            impact_position = None
            impact_type_id = None

        # Cross-check: our parsed record vs the wiki. A mismatch means one side is
        # stale, and a mapping built on stale data would edit the wrong row.
        #
        # The AP compared must be the AP of the section the payload came from.
        # For an explosion payload that is the explosion section's AP (R-36
        # Eruptor: explosion AP3, projectile AP4) - using the projectile's AP here
        # reported a false mismatch on every weapon whose two sections differ.
        if payload == "explosion":
            checks = {
                "damage": exp.get("explosion_damage") is None or exp["explosion_damage"] == d.damage,
                "durable": exp.get("explosion_damage") is None or exp["explosion_damage"] == d.durable_damage,
                "ap": exp.get("ap_direct") is None or exp["ap_direct"] == d.armor_penetration_per_angle[0],
                "radii": True,
            }
        else:
            checks = {
                "damage": parsed.get("standard") == d.damage,
                "durable": parsed.get("durable") == d.durable_damage,
                "ap": parsed.get("ap_direct") is None or parsed["ap_direct"] == d.armor_penetration_per_angle[0],
                "velocity": (
                    True if how == "arc"
                    else parsed.get("velocity") == match.speed
                ),
            }
        # `ammo` is the game's own string-table name for the projectile row and is
        # authoritative. It is absent whenever the match did not key on a
        # projectile (stats-only, arc, explosion) - 65 of 102 weapons.
        #
        # The wiki usually still names the ammo, so carry that separately rather
        # than filling `ammo` with text the game never uses. Two traps:
        #   * a trailing `P`/`P1` variant marker that is not part of the name;
        #   * pages with no ammo documented repeat the weapon name ("AR-2 P",
        #     "CB-9 P"), which is a placeholder, not an ammo type.
        ammo_game = eng.get(str(_name_key(pblob, match.row)), None) if match.row >= 0 else None
        ammo_wiki = None
        raw = parsed.get("ammo_raw") or ""
        if raw:
            cand = re.sub(r"\s+P\d*\s*$", "", raw.strip(), flags=re.I).strip()
            # A real ammo name carries its calibre ("9x70mm …", "20mm …",
            # "12.5mm …"). Without that check the parser picks up section labels
            # instead: AC-8 Autocannon's page has a shrapnel section whose label
            # is just "SHRAPNEL", which is not an ammo type at all.
            # Pages with no ammo documented repeat the weapon name ("AR-2 P",
            # "CB-9 P") - also a placeholder, not an ammo type.
            base = re.sub(r"^[A-Z]+/\w+\s+|\s+(Mk|MK)?\s*\w*$", "", title).strip().lower()
            looks_like_calibre = re.search(r"\d", cand) is not None
            # A weapon designation is not an ammo name: CB-9's page shows "CB-9 P"
            # and the title starts with "CB-9". Reject anything the title begins
            # with, which covers both the bare code and the full-name repeat.
            is_designation = title.lower().startswith(cand.lower())
            if (
                cand
                and looks_like_calibre
                and not is_designation
                and cand.lower() not in (title.lower(), base)
                and not re.fullmatch(r"[A-Z]+-\d+\s+[A-Za-z]+", cand)
            ):
                ammo_wiki = cand
        rows.append({
            "page": title,
            # TWO numbers, both needed, deliberately not interchanged:
            #   damage_index    - the row's type_id (the value at record +0).
            #                     Unique across the table. This is what the
            #                     runtime resolver matches on and what the GUI
            #                     shows, because it identifies the row.
            #   damage_position - the row's index in the array (0..633). This is
            #                     the number that addresses the row: the mod
            #                     writes at base + position*RECORD_SIZE, and the
            #                     projectile table's damage_info_type field
            #                     references positions too.
            # 519 of 634 rows have these differ. Every entry is checked below.
            "damage_index": match_index,
            "damage_position": match_position,
            "impact_damage_index": impact_type_id,
            "impact_damage_position": impact_position,
            "payload": payload,
            "projectile_row": match.row,
            "ammo": ammo_game,
            "ammo_wiki": ammo_wiki,
            "speed": match.speed,
            "damage": d.damage,
            "durable": d.durable_damage,
            "ap": d.armor_penetration_per_angle,
            "forces": [d.demolition_strength, d.force_strength, d.force_impulse],
            "wiki": parsed,
            "checks": checks,
            "matched_by": how,
            "verified": all(checks.values()),
        })

    out = ROOT / "data" / "weapon_names.json"

    # Annotate exclusivity. A damage row can serve several weapons (verified: the
    # 5.5x50mm FMJ row backs four), and editing a shared row changes all of them.
    # The user's requirement is "only my weapon", so every entry carries this so
    # the GUI can warn before generating a mod.
    owners: dict[int, list[str]] = {}
    # Key on damage_position, NOT damage_index.
    #
    # The two numbering systems disagree on 519 of 634 rows (position is the
    # array slot; index is the record's DamageInfoType id). Grouping on the
    # index reported the wrong peers wherever they differ - e.g. GL-21's row
    # was listed as 368 (its type_id) when the row actually written is 352.
    # The GUI shows the position, so the peers must be derived from the same
    # number or the warning names the wrong set of weapons.
    for r in rows:
        owners.setdefault(r["damage_position"], []).append(r["page"])
    for r in rows:
        peers = [p for p in owners[r["damage_position"]] if p != r["page"]]
        r["shared_with"] = peers
        r["exclusive"] = not peers

    # Same annotation for the impact segment of explosive weapons. It is a
    # separate damage record with its own owners, so a weapon can have an
    # exclusive explosion and a shared impact (or the reverse).
    impact_owners: dict[int, list[str]] = {}
    for r in rows:
        pos = r.get("impact_damage_position")
        if pos is not None:
            impact_owners.setdefault(pos, []).append(r["page"])
    for r in rows:
        pos = r.get("impact_damage_position")
        if pos is None:
            r["impact_shared_with"] = []
            r["impact_exclusive"] = True
        else:
            peers = [p for p in impact_owners[pos] if p != r["page"]]
            r["impact_shared_with"] = peers
            r["impact_exclusive"] = not peers

    impact_shared = {k: v for k, v in impact_owners.items() if len(v) > 1}
    out.write_text(
        json.dumps(
            {
                "weapons": rows,
                "unmatched": unmatched,
                "shared_damage_rows": {str(k): v for k, v in owners.items() if len(v) > 1},
                "shared_impact_rows": {str(k): v for k, v in impact_shared.items()},
            },
            indent=1,
        ),
        encoding="utf-8",
    )

    ok = [r for r in rows if r["verified"]]
    bad = [r for r in rows if not r["verified"]]
    shared = {k: v for k, v in owners.items() if len(v) > 1}
    print(f"matched {len(rows)} weapons | verified {len(ok)} | mismatch {len(bad)} | unmatched {len(unmatched)}")
    print(f"exclusive rows: {len(rows) - sum(len(v) for v in shared.values())} | shared rows: {len(shared)}")
    print(f"shared impact rows: {len(impact_shared)}")
    for r in bad[:15]:
        print(f"  MISMATCH {r['page']}: {r['checks']}")
    for t, why in unmatched[:10]:
        print(f"  unmatched {t}: {why}")
    print(f"wrote {out}")


def _name_key(blob: bytes, row: int) -> int:
    import struct
    from build_map import PROJECTILE_ARRAY_OFFSET, PROJECTILE_RECORD_SIZE
    off, _ = struct.unpack_from("<QQ", blob, PROJECTILE_ARRAY_OFFSET)
    base = PROJECTILE_ARRAY_OFFSET + off + row * PROJECTILE_RECORD_SIZE
    return struct.unpack_from("<i", blob, base + 8)[0]


if __name__ == "__main__":
    if "--build" in sys.argv:
        build()
    else:
        fetch()
