"""The Simplified-Chinese weapon names are the game's own, and are not guessed.

The GUI used to show the English page title (`GL-15 Evictor`) while a player running
Simplified Chinese sees `GL-15驱逐者`. The name is readable from the game's own
`.strings` resources, but reading it is not a lookup: a weapon's name is two keys
(designation + nickname) and the nickname alone is ambiguous.

These tests hold the two rules that resolve it, and - more importantly - hold the
cases where a naive implementation produces a WRONG name rather than no name:

  * `Adjudicator` -> `审判者`, not `裁决者`
  * `Spear`       -> `飞矛`, not `之矛`
  * `Impact`      -> `冲击弹`, not `着陆` (the value a first-match lookup returned
                     during development, which is simply a different thing)
  * `Frag`        -> `破片弹`, not `破片手雷`
  * `Stalwart`    -> `盟友`, not `轻机枪`
  * `Defender`    -> `防卫者`, not `捍卫者`

A wrong weapon name is worse than an English one, because the user has no way to
tell it is wrong. So every rule below is paired with a test that it can still say
"no".

Run:  python tests/test_zh_names.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import zh_names as Z  # noqa: E402

PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f"  -- {detail}" if detail else ""))


def main() -> int:
    strings, by_text, key_packs = Z.load()
    pages = Z._all_pages()
    names = Z.build()

    # -- coverage --------------------------------------------------------
    check("every weapon has a Simplified name",
          len(names) == len(pages), f"{len(names)} of {len(pages)}")
    check("there are 99 weapons to name", len(pages) == 99, str(len(pages)))

    # -- the game really does ship a Simplified pack ---------------------
    langs = set()
    for v in strings.values():
        if isinstance(v, dict):
            langs.update(v.keys())
    check("the string table carries Simplified Chinese", Z.LANG in langs)
    check("it still carries Traditional too", "Chinese (Traditional)" in langs)

    # -- the six ambiguous cases resolve to the checked values -----------
    # Each of these has a same-text key that yields a DIFFERENT Chinese name.
    # Asserting the wrong-looking alternative is absent is the point: a
    # first-match lookup passes a coverage count and fails these.
    expected = {
        "BR-14 Adjudicator": "BR-14审判者",
        "FAF-14 Spear": "FAF-14飞矛",
        "G-16 Impact": "G-16冲击弹",
        "G-6 Frag": "G-6破片弹",
        "M-105 Stalwart": "M-105盟友",
        "SMG-37 Defender": "SMG-37防卫者",
    }
    for page, want in expected.items():
        got = names.get(page)
        check(f"{page} -> {want}", got == want, f"got {got!r}")

    wrong = {
        "BR-14 Adjudicator": "裁决者",
        "FAF-14 Spear": "之矛",
        "G-16 Impact": "着陆",
        "G-6 Frag": "破片手雷",
        "M-105 Stalwart": "轻机枪",
        "SMG-37 Defender": "捍卫者",
    }
    for page, bad in wrong.items():
        check(f"{page} does not take the decoy {bad}",
              names.get(page, "").replace(page.split()[0], "") != bad,
              f"got {names.get(page)!r}")

    # -- the rule that separates them is real, not luck ------------------
    # Co-location must actually narrow: the decoy key must NOT share packs with
    # the designation key. If this stops holding, the tests above pass by
    # accident and the next ambiguous weapon gets a wrong name.
    for designation, decoy_key in (("BR-14", "1536742308"),      # 裁决者
                                   ("FAF-14", "1013395843"),     # 之矛
                                   ("SMG-37", "1185375349")):    # 捍卫者
        dkeys = sorted(Z._keys_for(designation, by_text))
        dpacks = key_packs.get(dkeys[0], set())
        check(f"{designation}: the decoy key is filed apart from it",
              not (key_packs.get(decoy_key, set()) & dpacks),
              f"decoy shares {len(key_packs.get(decoy_key, set()) & dpacks)} packs")

    # -- no space between designation and name ---------------------------
    # The game writes composite names closed up (`B-22模范公民`). A space may
    # still appear INSIDE the official name - `TD-220堡垒MK XVI` is correct,
    # because the game's own nickname carries one - so the check is that the
    # designation is not the thing that introduced a separator.
    check("the designation is not followed by a space",
          all(not n.startswith(p.split()[0] + " ") for p, n in names.items()),
          next((n for p, n in names.items()
                if n.startswith(p.split()[0] + " ")), ""))
    check("GL-15 Evictor reads as one token",
          names["GL-15 Evictor"] == "GL-15驱逐者", names.get("GL-15 Evictor"))
    check("a space inside the official nickname is preserved",
          names.get("TD-220 Bastion MK XVI") == "TD-220堡垒MK XVI",
          names.get("TD-220 Bastion MK XVI"))

    # -- the GUI shows the name, and search still finds the English one ----
    html = (ROOT / "gui/templates/index.html").read_text(encoding="utf-8")
    check("the list and panel use the display-name helper",
          "function dispName(w) { return w.name_zh || w.page; }" in html)
    check("the list renders the display name",
          "${dispName(w)}" in html, "the list still renders w.page")
    check("the panel title uses the display name",
          "<h2>${dispName(w)}</h2>" in html)
    # Search must keep working in both scripts: the visible name is Chinese, but
    # the user may know a weapon by its English title from the wiki. The first
    # version of this check looked for `w.name_zh` anywhere in the file, which
    # still passes when only the search haystack loses it - `dispName` contains
    # the same token. Scope the assertion to the haystack itself.
    hay = html.split("const hay = ")[1].split(";")[0]
    check("search matches the Chinese name", "w.name_zh" in hay, hay)
    check("search matches the English title too", "w.page" in hay, hay)
    check("search matches the ammo string", "w.ammo" in hay, hay)

    # -- the API actually serves the field the template reads --------------
    # The DOM test talks to a live server process, so mutating app.py does not
    # affect it; a Flask test client exercises the real route in-process and
    # does catch the field going missing.
    sys.path.insert(0, str(ROOT / "gui"))
    import app as gui_app
    with gui_app.app.test_client() as client:
        payload = client.get("/api/weapons").get_json()
    served = payload["weapons"]
    check("the API serves every weapon", len(served) == 99, str(len(served)))
    check("the API serves name_zh",
          all("name_zh" in w for w in served),
          "field missing from the payload")
    check("the served names match the built map",
          all(w.get("name_zh") == names.get(w["page"]) for w in served),
          "a served name differs from data/weapon_names.json")
    check("the API still serves page as the identity",
          all(w.get("page") for w in served))
    check("an unknown page gives None",
          Z.zh_name("ZZ-99 Nonexistent", strings, by_text, key_packs) is None)
    check("an empty page gives None",
          Z.zh_name("", strings, by_text, key_packs) is None)

    # A nickname with two genuinely different readings and no designation to
    # arbitrate must refuse. `Frag` has three keys reading 破片弹/破片手雷/破片弹;
    # stripped of its designation the majority still applies, so use a synthetic
    # pair instead - two keys, two readings, no majority.
    fake_strings = {
        "1": {"English (US)": "Alpha", Z.LANG: "甲"},
        "2": {"English (US)": "Alpha", Z.LANG: "乙"},
    }
    fake_by_text = {"Alpha": {"1", "2"}, "ALPHA": {"1", "2"}}
    fake_packs = {"1": {"a"}, "2": {"b"}}
    check("a genuine tie is refused rather than guessed",
          Z._resolve("", "Alpha", fake_strings, fake_by_text, fake_packs) is None)

    # A majority does decide, which is what settles G-6 Frag.
    fake_strings["3"] = {"English (US)": "Alpha", Z.LANG: "甲"}
    fake_by_text["Alpha"] = {"1", "2", "3"}
    fake_by_text["ALPHA"] = {"1", "2", "3"}
    check("a majority decides",
          Z._resolve("", "Alpha", fake_strings, fake_by_text, fake_packs) == "甲")

    # Co-location must OUTRANK the majority, and this is the only test that shows
    # it does. On today's six ambiguous weapons the majority happens to agree with
    # co-location every time, so deleting the co-location rule entirely still
    # passes every other check here - verified by mutation. A future weapon whose
    # decoy keys outnumber the real one is exactly the case it exists for, so it
    # is pinned synthetically: two decoys filed away from the designation, one
    # real key filed beside it.
    tie_strings = {
        "d": {"English (US)": "Delta"},          # the designation
        "n1": {"English (US)": "Beta", Z.LANG: "对"},
        "n2": {"English (US)": "Beta", Z.LANG: "错"},
        "n3": {"English (US)": "Beta", Z.LANG: "错"},
    }
    tie_by_text = {"Delta": {"d"}, "Beta": {"n1", "n2", "n3"}}
    tie_packs = {"d": {"pack-a"}, "n1": {"pack-a"},
                 "n2": {"pack-b"}, "n3": {"pack-c"}}
    got = Z._resolve("Delta", "Beta", tie_strings, tie_by_text, tie_packs)
    check("co-location outranks the majority", got == "对",
          f"got {got!r} - the majority rule won, so a decoy could outvote the real name")

    # -- the split is data-driven, not a regex ---------------------------
    # `M7S`, `M6C/SOCOM`, `B/MD`, `G/40-K` and `StA-X3` are all real designations
    # that a `<letters>-<digits>` pattern rejects.
    for page, want_designation in (("M7S SMG", "M7S"),
                                   ("M6C/SOCOM Pistol", "M6C/SOCOM"),
                                   ("B/MD C4 Pack", "B/MD"),
                                   ("G/40-K Melta Mine", "G/40-K"),
                                   ("StA-X3 W.A.S.P. Launcher", "StA-X3"),
                                   ("M-1000 Maxigun", "M-1000"),
                                   ("R-63CS Diligence Counter Sniper", "R-63CS")):
        split = Z._split(page, by_text)
        check(f"{page} splits after {want_designation}",
              split is not None and split[0] == want_designation,
              str(split))

    # -- the names are Simplified, verified against the Traditional pack --
    # Character sets were tried first and were wrong in both directions: 鬣 and
    # 震/猛/爪 are identical in both scripts, so a "Simplified-only characters"
    # set misses names and a "Traditional-only" set falsely flags them. Comparing
    # against the Traditional reading of the same weapon is exact.
    def name_in(page: str, lang: str) -> str | None:
        split = Z._split(page, by_text)
        if not split:
            return None
        designation, nickname = split
        cands = sorted(Z._keys_for(nickname, by_text))
        dkeys = sorted(Z._keys_for(designation, by_text)) if designation else []
        if dkeys:
            packs = key_packs.get(dkeys[0], set())
            colo = [c for c in cands if key_packs.get(c, set()) & packs]
            if colo:
                cands = colo
        texts = {strings[c].get(lang) for c in cands}
        texts.discard(None)
        if len(texts) != 1:
            return None
        return (designation + texts.pop()) if designation else texts.pop()

    differ = 0
    for page in pages:
        s = name_in(page, Z.LANG)
        t = name_in(page, "Chinese (Traditional)")
        if s and t and s != t:
            differ += 1
    check("most names differ from their Traditional reading",
          differ > len(pages) * 0.7, f"{differ} of {len(pages)} differ")

    # Spot-check the specific pairs, including ones where the two scripts share
    # every character (R-4鬣狗 vs R-4土狼 differs by wording, not by script).
    for page, simp, trad in (
        ("GL-15 Evictor", "GL-15驱逐者", "GL-15驅離者"),
        ("R-4 Hyena", "R-4鬣狗", "R-4土狼"),
        ("AR-23C Liberator Concussive", "AR-23C震荡“解放者”", "AR-23C震盪解放者"),
        ("LAS-58 Talon", "LAS-58猛爪", "LAS-58利爪"),
    ):
        check(f"{page} matches the Simplified reading",
              names.get(page) == simp, f"got {names.get(page)!r}")
        check(f"{page} is not the Traditional reading",
              names.get(page) != trad, f"got {names.get(page)!r}")

    # And no name in the map equals a Traditional-only reading of the same weapon.
    from_trad = [p for p in pages
                 if names.get(p) and names.get(p) == name_in(p, "Chinese (Traditional)")
                 and name_in(p, "Chinese (Simplified)") != names.get(p)]
    check("no name was taken from the Traditional pack", not from_trad,
          str(from_trad[:3]))

    print(f"\n{PASS} PASS, {FAIL} FAIL")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
