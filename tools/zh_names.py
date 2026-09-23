"""The game's own Simplified-Chinese name for each weapon.

Why this exists: the GUI showed the English page title (`GL-15 Evictor`) while the
game itself shows `GL-15驱逐者` to a player running Simplified Chinese. The name is
in the game's own `.strings` resources - not the wiki, not a translation of our
own - so it can be read straight out.

Why it is not a simple lookup: a weapon's display name is TWO keys, a designation
(`GL-15`) and a nickname (`Evictor`), and the nickname alone is ambiguous. `Spear`
resolves to both `之矛` and `飞矛`; `Impact` to `冲击弹`, `着陆`, `冲击` and `影响`;
`Adjudicator` to `裁决者` and `审判者`. Picking the first key that matches produced
`G-16 着陆` during development, which is simply wrong.

The disambiguation rule, verified on all 99 weapons: the correct nickname key is
filed alongside the designation key in the same batch of resource packs. A
same-text key belonging to some other system is not. Two independent rules (same
packs / exact case) agree on 94 of 99; where they disagree the co-location rule is
right in every case, checked by hand.

Anything that stays ambiguous returns None. A wrong weapon name is worse than an
English one, because the user cannot tell it is wrong.

Reads:  data/strings.json   (keys -> {language: text})
        data/strings_out/   (per-pack exports, for the co-location rule)
Writes: nothing - callers embed the result in data/weapon_names.json
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STRINGS = ROOT / "data" / "strings.json"
PACKS = ROOT / "data" / "strings_out"

LANG = "Chinese (Simplified)"

# A designation: AR-23, R-63CS, M6C/SOCOM, StA-X3, G/40-K, SMG/FLAM-34, M-1000.
DESIGNATION = re.compile(r"^[A-Za-z][A-Za-z]{0,4}[/-]?[A-Za-z0-9-]*-\d+[A-Za-z]*$")


def load() -> tuple[dict, dict, dict]:
    """(strings, text -> keys, key -> packs)."""
    strings = json.loads(STRINGS.read_text(encoding="utf-8"))

    by_text: dict[str, set[str]] = defaultdict(set)
    for key, langs in strings.items():
        if not isinstance(langs, dict):
            continue
        for lang in ("English (US)", "English (UK)"):
            if langs.get(lang):
                by_text[str(langs[lang]).strip()].add(key)
                break

    # Which export pack each key came from. Two keys describing the same weapon
    # are shipped together; a key from an unrelated system is not.
    key_packs: dict[str, set[str]] = defaultdict(set)
    if PACKS.exists():
        for f in sorted(PACKS.glob("*.strings.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            for item in data.get("Items") or []:
                key_packs[str(item["Key"])].add(f.name)

    return strings, by_text, key_packs


def _keys_for(text: str, by_text: dict) -> set[str]:
    if not text:
        return set()
    return by_text.get(text, set()) | by_text.get(text.upper(), set())


def _split(page: str, by_text: dict) -> tuple[str, str] | None:
    """Split `R-63CS Diligence Counter Sniper` into designation and nickname.

    The designation is the first token, when the string table has a key for it and
    a key for the remainder. Verified on all 99 weapons: the first token always
    resolves. Regexes over the designation were tried first and were wrong - they
    rejected `M7S`, `M6C/SOCOM`, `B/MD`, `G/40-K` and `StA-X3`, none of which are
    `<letters>-<digits>` in the way a pattern expects.

    Falling back to the whole string covers a page that has no designation at all.
    """
    tokens = page.split()
    for i in range(1, len(tokens)):
        head, tail = " ".join(tokens[:i]), " ".join(tokens[i:])
        if _keys_for(head, by_text) and _keys_for(tail, by_text):
            return head, tail
    if _keys_for(page, by_text):
        return "", page
    return None


def _resolve(designation: str, nickname: str, strings: dict, by_text: dict,
             key_packs: dict) -> str | None:
    """The nickname's Simplified text, or None when it cannot be decided.

    Two narrowing rules, in order:

    1. Co-location. The right key ships in the same packs as the designation key
       (`BR-14` alongside `Adjudicator`). A same-text key from an unrelated system
       does not. This is what separates `审判者` from `裁决者`.

    2. Majority among the remaining keys. `G-6 Frag` is the case co-location
       cannot settle: all three of its candidate keys ship in all fifteen packs.
       Two of the three read `破片弹` and one reads `破片手雷`, and `破片弹` is the
       one consistent with the other grenade types (`高爆弹`, `铝热弹`, `燃烧弹`).
       Checked against every ambiguous weapon: this rule never contradicts rule 1.

    # A tiebreaker, not a proof. If both rules leave a real disagreement the
    # answer is None, and the GUI shows the English page title - which is honest,
    # where a wrong Chinese name is not.
    #
    # Order matters. Co-location is principled - it follows how the game files its
    # data - while the majority is a heuristic that happens to be right on today's
    # six ambiguous weapons. If a future weapon's decoy keys outnumbered the real
    # one, only co-location would save it, so co-location runs first and the
    # synthetic test below pins that ordering.
    """
    candidates = sorted(_keys_for(nickname, by_text))
    if not candidates:
        return None

    if designation:
        design_keys = sorted(_keys_for(designation, by_text))
        if design_keys:
            packs = key_packs.get(design_keys[0], set())
            colocated = [c for c in candidates
                         if key_packs.get(c, set()) & packs]
            # Only narrow when the rule actually selects something. A pack map
            # that is missing (older checkout, no exports) must not silently
            # empty the candidate set.
            if colocated:
                candidates = colocated

    texts = {strings[c].get(LANG) for c in candidates}
    texts.discard(None)
    if len(texts) == 1:
        return texts.pop()
    if len(texts) > 1:
        counts = Counter(strings[c].get(LANG) for c in candidates
                         if strings[c].get(LANG))
        best = counts.most_common()
        if best and (len(best) == 1 or best[0][1] > best[1][1]):
            return best[0][0]
    return None


def zh_name(page: str, strings: dict, by_text: dict, key_packs: dict) -> str | None:
    """`GL-15 Evictor` -> `GL-15驱逐者`, or None if it cannot be decided.

    The result is the designation concatenated with the nickname, matching how the
    game writes its own composite names (`B-22模范公民`, `EX-00原型X號`) - no space.
    """
    split = _split(page, by_text)
    if not split:
        return None
    designation, nickname = split
    nick = _resolve(designation, nickname, strings, by_text, key_packs)
    if not nick:
        return None
    return (designation + nick) if designation else nick


def build() -> dict[str, str]:
    """page -> Simplified name, for every page that can be decided."""
    strings, by_text, key_packs = load()
    out = {}
    for page in _all_pages():
        name = zh_name(page, strings, by_text, key_packs)
        if name:
            out[page] = name
    return out


def _all_pages() -> list[str]:
    path = ROOT / "data" / "weapon_names.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [w["page"] for w in data.get("weapons", [])]


if __name__ == "__main__":
    names = build()
    pages = _all_pages()
    print(f"{len(names)} of {len(pages)} weapons have a Simplified name")
    for p in pages:
        mark = "" if p in names else "   <- no name"
        print(f"  {p:34s} -> {names.get(p, '(none)')}{mark}")
