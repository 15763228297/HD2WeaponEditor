"""Shared-impact must be visible in the weapon list, not only in the detail panel.

The bug: an explosive weapon has two damage records - impact and explosion -
with independent owners. The list badge and the "exclusive only" filter both
looked at the explosion record alone. Thirteen weapons have a shared impact
row; five of them have an exclusive explosion row too, so they showed no badge
at all and passed the "exclusive only" filter, despite editing their impact
half changing other weapons.

    PLAS-15 Loyalist: explosion row exclusive, impact row shared with
    PLAS-1 Scorcher and PLAS-39 Accelerator Rifle - listed as exclusive.

Run:  python tests/test_shared_impact_badge.py
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

    names = json.loads(
        (ROOT / "data" / "weapon_names.json").read_text(encoding="utf-8"))
    weapons = names["weapons"]

    # The exact case reported: exclusive explosion, shared impact.
    hidden = [
        w for w in weapons
        if w["exclusive"] and not w.get("impact_exclusive", True)
    ]
    print("== weapons whose impact row is shared but that look exclusive ==")
    for w in hidden:
        print(f"     {w['page']}  impact shared with "
              f"{w.get('impact_shared_with')}")
    check("the dataset contains this case (it is why the badge was added)",
          len(hidden) > 0, str(len(hidden)))
    check("PLAS-15 Loyalist is among them",
          any("PLAS-15" in w["page"] for w in hidden),
          str([w["page"] for w in hidden]))

    print()
    print("== the API exposes impact exclusivity to the list ==")
    # Ask the running server, so this covers the serializer too, not just the
    # data file.
    try:
        sys.path.insert(0, str(ROOT))
        import gui.app as app_mod
        # api_weapons() reads request context in some paths; push an app
        # context so it can be called directly rather than over HTTP.
        with app_mod.app.app_context():
            payload = app_mod.api_weapons().get_json()
        rows = {w["page"]: w for w in payload["weapons"]}
        target = next(p for p in rows if "PLAS-15" in p)
        check("the API sends impact_exclusive",
              "impact_exclusive" in rows[target], str(list(rows[target])[:12]))
        check("PLAS-15 is marked as NOT impact-exclusive",
              rows[target].get("impact_exclusive") is False,
              str(rows[target].get("impact_exclusive")))
        check("the API also sends who shares it",
              bool(rows[target].get("impact_shared_with")),
              str(rows[target].get("impact_shared_with")))
    except Exception as exc:
        check("the API exposes impact exclusivity", False, f"{type(exc).__name__}: {exc}")

    print()
    print("== the badge and filter use both halves ==")
    html = (ROOT / "gui" / "templates" / "index.html").read_text(encoding="utf-8")
    check("the list badges mark a shared impact row",
          "直击共享" in html, "no impact badge in the template")
    check("the list badges mark a shared explosion row",
          "爆炸共享" in html, "no explosion badge in the template")
    check("the exclusive filter requires BOTH halves to be unshared",
          "w.exclusive && w.impact_exclusive" in html,
          "filter still checks only the explosion row")
    # Only explosive weapons have two segments. Labelling a plain projectile
    # weapon's single row "explosion shared" (as AR-23 was for a while) is
    # nonsense - it has no explosion.
    check("the explosion-shared badge is gated on the payload type",
          'w.payload === "explosion"' in html
          and "爆炸共享" in html,
          "the badge does not check whether the weapon is explosive")
    check("the impact badge is gated on the payload type",
          'w.payload === "explosion" && !w.impact_exclusive' in html,
          "a non-explosive weapon could be labelled as having a shared impact")

    print()
    print("== the badge wording matches the weapon type ==")
    # Non-explosive weapons have one row, so they get the plain wording.
    plain = [w for w in weapons
             if w["payload"] != "explosion" and not w["exclusive"]]
    print(f"     {len(plain)} non-explosive weapons share their only row")
    check("there are non-explosive weapons with a shared row", len(plain) > 0,
          str(len(plain)))
    check("none of them has an impact row to share",
          all(w.get("impact_damage_position") is None for w in plain),
          str([w["page"] for w in plain
               if w.get("impact_damage_position") is not None]))
    check("all of them are marked impact-exclusive (no such segment)",
          all(w.get("impact_exclusive", True) for w in plain),
          str([w["page"] for w in plain if not w.get("impact_exclusive", True)]))

    print()
    print("== the counts agree with the data ==")
    verified = [w for w in weapons if w["verified"]]
    exp_shared = sum(1 for w in verified if not w["exclusive"])
    imp_shared = sum(1 for w in verified if not w.get("impact_exclusive", True))
    fully = sum(1 for w in verified
                if w["exclusive"] and w.get("impact_exclusive", True))
    print(f"     verified {len(verified)}: explosion-shared {exp_shared}, "
          f"impact-shared {imp_shared}, fully exclusive {fully}")
    # The old filter counted only the explosion row and overstated this by 5.
    old_exclusive = sum(1 for w in verified if w["exclusive"])
    check("excluding shared-impact shrinks the exclusive set",
          fully < old_exclusive, f"{fully} vs {old_exclusive}")
    check("the difference is exactly the hidden-impact cases",
          old_exclusive - fully == len(hidden),
          f"{old_exclusive - fully} vs {len(hidden)}")

    print()
    print(f"test_shared_impact_badge: "
          f"{'PASS' if not base_mod.failures else 'FAIL'} ({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
