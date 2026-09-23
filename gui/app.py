"""Weapon stat editor - local web GUI.

Run:  python gui/app.py        then open http://127.0.0.1:8777

Design notes that matter:

* The weapon list comes from `data/weapon_names.json`, which is built by
  `tools/wiki_names.py` and cross-checked against the wiki (four stat fields per
  weapon). Entries that failed that check are shown but flagged - never silently
  offered as editable, because a wrong mapping means the user edits another
  weapon's numbers.
* Each entry carries `payload` (projectile / explosion) and `exclusive` with
  `shared_with`. Editing a shared row changes every weapon on it, so the UI warns
  before generating. R-4's row is exclusive, which is what makes "only my weapon"
  work for it.
* Nothing here writes to the game. The GUI reads parsed tables and produces a
  mod package; deployment stays a separate, explicit step.
"""

from __future__ import annotations

import json
import sys
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, render_template, request

def _resolve_root() -> Path:
    """Where the project's data and tools live.

    Running from source, that is the repo root next to `gui/`. In a frozen
    PyInstaller build, `__file__` points into the temp extraction directory and
    everything bundled with `--add-data` lands in `sys._MEIPASS`, so the same
    expression would resolve to a folder that does not exist.
    """
    frozen = getattr(sys, "frozen", False)
    meipass = getattr(sys, "_MEIPASS", None)
    if frozen and meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


ROOT = _resolve_root()
# In a frozen build __file__ sits in the temp extraction dir, so ROOT alone
# does not find `tools/` - it must be added to sys.path relative to whatever
# root was resolved. Without this the frozen app raises
# "ModuleNotFoundError: No module named 'gen_mod'" the moment it generates.
sys.path.insert(0, str(ROOT / "tools"))

# Same frozen caveat as the root: __file__ is inside the extraction dir there,
# and the templates are bundled alongside it via --add-data.
app = Flask(__name__,
            template_folder=str(_resolve_root() / "gui" / "templates"))
# Template edits must show up on the next page load. Without this Flask caches
# the compiled template per process, and the served page silently keeps running
# old code while the file on disk looks current - which cost a debugging cycle:
# the deep-link feature was on disk, absent from what the browser received.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True


def output_dir() -> Path:
    """Where the generated ZIP should be written.

    Frozen builds unpack into a temp dir that is deleted on exit, so writing
    next to the bundle would hand the user a path that no longer exists the
    moment they close the app. Write beside the executable instead.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "build"
    return ROOT / "build"


def load_damage_records() -> dict:
    """Damage table keyed by position, for looking up a row's live values."""
    path = ROOT / "data" / "damage_records.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw if isinstance(raw, list) else raw.get("records", raw)
    if isinstance(rows, dict):
        rows = [rows[k] for k in sorted(rows, key=int)]
    # Keyed by array position - the same numbering the mod writes to, never the
    # record's type_id, which differs on most rows.
    return {i: r for i, r in enumerate(rows)}


def lookup_values(position: int | None) -> dict | None:
    """Read a damage row's values, or None when the weapon has no such row."""
    if position is None:
        return None
    rec = load_damage_records().get(position)
    if rec is None:
        return None
    return {
        "damage": rec.get("damage"),
        "durable": rec.get("durable_damage"),
        "ap": rec.get("armor_penetration_per_angle"),
    }


def load_weapons() -> dict:
    path = ROOT / "data" / "weapon_names.json"
    if not path.exists():
        raise SystemExit(
            f"{path} not found - run `python tools/wiki_names.py --build` first"
        )
    return json.loads(path.read_text(encoding="utf-8"))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/weapons")
def api_weapons():
    data = load_weapons()
    out = []
    for w in data["weapons"]:
        out.append({
            "page": w["page"],
            # The game's own Simplified-Chinese name, for display. `page` stays
            # the identity - matching and generation key on it - so this is an
            # addition, not a rename. Absent when the name could not be decided,
            # and the template then falls back to `page` rather than inventing one.
            "name_zh": w.get("name_zh"),
            # Two numbers, both surfaced: `damage_index` is the row's type_id
            # (what identifies it) and `damage_position` is its array index
            # (what addresses it). They differ on 519 of 634 rows.
            "damage_index": w["damage_index"],
            "damage_position": w.get("damage_position"),
            # Explosion-payload weapons carry two rows: the payload the user
            # wants to edit, and the projectile's impact token. The detail panel
            # names both, so a missing field shows as "#undefined" rather than
            # silently omitting the distinction.
            "impact_damage_index": w.get("impact_damage_index"),
            "impact_damage_position": w.get("impact_damage_position"),
            "impact_shared_with": w.get("impact_shared_with", []),
            "impact_exclusive": w.get("impact_exclusive", True),
            # The impact segment has its own values. Without them the panel could
            # only show a row number, which is exactly the gap the user hit:
            # half of an explosive weapon's damage was invisible.
            "impact_values": lookup_values(w.get("impact_damage_position")),
            "payload": w["payload"],
            # `ammo` is the game's own name (authoritative, 37 weapons);
            # `ammo_wiki` is the wiki's name (49 weapons) and is only shown when
            # the game has none - the two differ in casing and sometimes wording,
            # so they are never merged into one field.
            "ammo": w["ammo"],
            "ammo_wiki": w.get("ammo_wiki"),
            "speed": w["speed"],
            "damage": w["damage"],
            "durable": w["durable"],
            "ap": w["ap"],
            "forces": w["forces"],
            "verified": w["verified"],
            "exclusive": w["exclusive"],
            "shared_with": w.get("shared_with", []),
            "matched_by": w["matched_by"],
            # A hand-registered row is not derived from the wiki, so the panel
            # must be able to say where its numbers came from. Without this the
            # GUI shows the same provenance block as a derived weapon and leaves
            # the comparison fields empty, which reads as "the wiki agrees"
            # rather than "there is no wiki figure for this weapon".
            "manual_source": w.get("manual_source"),
            "manual_notes": w.get("manual_notes", []),
            # Raw wiki figures, so the UI can show provenance rather than
            # presenting parsed numbers as if they were beyond question.
            #
            # Both segments are carried. An explosive weapon's wiki page keeps
            # its direct-hit numbers at the top level and its explosion numbers
            # under `explosion`, and the panel compares each half against its
            # own source. Sending only the top level made the explosion half
            # compare against the direct-hit figures - GR-8 rendered
            # "肉伤 150 / 3200", pairing the game's explosion damage with the
            # wiki's direct-hit damage as though they were the same quantity.
            "wiki": {
                "velocity": w["wiki"].get("velocity"),
                "standard": w["wiki"].get("standard"),
                "durable": w["wiki"].get("durable"),
                "ap": w["wiki"].get("ap_direct"),
                "explosion": {
                    "damage": (w["wiki"].get("explosion") or {}).get("explosion_damage"),
                    "ap": (w["wiki"].get("explosion") or {}).get("ap_direct"),
                    "inner_radius": (w["wiki"].get("explosion") or {}).get("inner_radius"),
                    "outer_radius": (w["wiki"].get("explosion") or {}).get("outer_radius"),
                    "shockwave_radius": (w["wiki"].get("explosion") or {}).get("shockwave_radius"),
                },
            },
        })
    out.sort(key=lambda w: (not w["verified"], not w["exclusive"], w["page"]))
    return jsonify({
        "weapons": out,
        "shared_rows": data.get("shared_damage_rows", {}),
        "unmatched": data.get("unmatched", []),
        # Whether this data still describes the installed game build. A stale
        # map is not an error the tool can fix, but it is the difference between
        # "the mod did nothing" and "the mod cannot work on this build" - so the
        # UI says which one it is before the user spends a game launch on it.
        "build": _build_status(),
    })


def _build_status() -> dict:
    """Compare the data's recorded game build against the installed one.

    Never raises: a failure to determine the build must not stop the tool from
    listing weapons. An unknown status is reported as unknown.
    """
    try:
        import build_fingerprint

        result = build_fingerprint.check()
        return {
            "status": result.get("status", "unknown"),
            "message": result.get("message", ""),
            "data_version": (result.get("recorded") or {}).get("exe_version"),
            "game_version": (result.get("installed") or {}).get("exe_version"),
        }
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        return {"status": "unknown", "message": f"无法确认数据对应的游戏版本：{exc}",
                "data_version": None, "game_version": None}


def _angles_of(payload: dict) -> list[int] | None:
    """The three penetration tiers a request carries, or None if it carries none.

    Two shapes are accepted because the GUI sends `angles` and older callers
    (and the CLI-shaped payloads in the tests) send a single `ap`. Returning None
    rather than a default lets the caller's own error message explain what was
    missing, instead of silently substituting a tier.
    """
    angles = payload.get("angles")
    if isinstance(angles, list) and len(angles) == 3:
        return [int(a) for a in angles]
    if payload.get("ap") is not None:
        return [int(payload["ap"])] * 3
    return None


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """Build a mod from the values the user entered.

    Every refusal here is a refusal to edit the wrong thing, so the checks are
    deliberate rather than defensive: the weapon's mapping must have passed the
    wiki cross-check, its damage row must be used by this weapon alone, and at
    least one field must actually differ from the current value.
    """
    payload = request.get_json(silent=True) or {}
    page = payload.get("weapon")
    edits_arg = payload.get("edits")
    # The multi-weapon form carries its own per-entry validation; only the
    # single-weapon form needs a name up front.
    if not page and not edits_arg:
        return jsonify({"ok": False, "error": "未指定武器"}), 400

    if page:
        try:
            damage = int(payload.get("damage"))
            durable = int(payload.get("durable"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "肉伤、耐伤必须为数值"}), 400

        # Penetration arrives either as one tier (`ap`) or as the three
        # per-angle tiers (`angles`). The single value stays supported because
        # most weapons use one tier across all angles; the list exists because
        # the game's own rows often differ per angle.
        angles = payload.get("angles")
        if angles is None:
            try:
                angles = [int(payload.get("ap"))] * 3
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "穿甲必须为数值"}), 400
        else:
            if not isinstance(angles, list) or len(angles) != 3:
                return jsonify({"ok": False,
                                "error": "angles 需要三个值（直射/小角/大角）"}), 400
            try:
                angles = [int(a) for a in angles]
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "穿甲必须为数值"}), 400

        if not (0 <= damage <= 100000 and 0 <= durable <= 100000):
            return jsonify({"ok": False, "error": "伤害数值超出允许范围（0..100000）"}), 400
        if not all(0 <= a <= 10 for a in angles):
            return jsonify({"ok": False, "error": "穿甲等级需在 0..10 之间"}), 400

    try:
        import importlib
        import gen_mod
        importlib.reload(gen_mod)

        # Accept both shapes so the older single-weapon client keeps working:
        #   {"weapon": ..., "damage": ...}              - one weapon
        #   {"edits": [{weapon, damage, durable, ap}]}  - several, one scan
        # Accept three shapes:
        #   {"weapon":..., "damage":...}                 - one weapon
        #   {"edits":[{weapon,damage,durable,ap}]}       - several, one scan
        # Each edit may carry "segment": "impact" for the projectile half of an
        # explosive weapon, and "allow_shared": true to opt in to changing a
        # damage row that other weapons also use.
        payload = request.get_json(silent=True) or {}
        edits = payload.get("edits")
        if edits:
            specs = []
            for e in edits:
                seg = e.get("segment", "damage")
                allow = bool(e.get("allow_shared", False))
                angles = _angles_of(e)
                if seg == "impact":
                    specs.append(gen_mod.build_impact_spec(
                        e["weapon"], damage=int(e["damage"]),
                        durable=int(e["durable"]), ap_angles=angles,
                        allow_shared=allow))
                else:
                    specs.append(gen_mod.build_spec(
                        e["weapon"], damage=int(e["damage"]),
                        durable=int(e["durable"]), ap_angles=angles,
                        allow_shared=allow))
        else:
            allow = bool(payload.get("allow_shared", False))
            angles = _angles_of(payload)
            if payload.get("segment") == "impact":
                specs = [gen_mod.build_impact_spec(
                    payload.get("weapon"),
                    damage=int(payload["damage"]),
                    durable=int(payload["durable"]),
                    ap_angles=angles,
                    allow_shared=allow)]
            else:
                specs = [gen_mod.build_spec(
                    payload.get("weapon"),
                    damage=int(payload["damage"]),
                    durable=int(payload["durable"]),
                    ap_angles=angles,
                    allow_shared=allow)]

        sources = {
            name: (gen_mod.ROOT / "mod_template" / "src" / name).read_text(encoding="utf-8")
            for name in gen_mod.MODULES
        }
        source = gen_mod.render_module(specs, sources)
        bytecode = gen_mod.compile_source(source, chunkname=f"={gen_mod.SLOT}")
        blob = gen_mod.build_archive(bytecode, gen_mod.SLOT)

        out_dir = output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / gen_mod.ARCHIVE_STEM).write_bytes(blob)
        (out_dir / "generated.lua").write_text(source, encoding="utf-8")
        zip_path = gen_mod.package(out_dir, blob, specs[0])
    except SystemExit as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001 - surface the reason, never a blank 500
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500

    return jsonify({
        "ok": True,
        "weapon": specs[0].weapon,
        "weapons": [sp.weapon for sp in specs],
        "changes": specs[0].changes,
        "baseline": specs[0].baseline,
        "edits": [{"weapon": sp.weapon, "changes": sp.changes} for sp in specs],
        "slot": gen_mod.SLOT,
        "slot_hash": f"0x{gen_mod.resource_hash(gen_mod.SLOT):016X}",
        "archive_bytes": len(blob),
        "archive_sha256": __import__("hashlib").sha256(blob).hexdigest(),
        "zip": str(zip_path),
    })


@app.route("/api/ping")
def api_ping():
    """Used by the DOM test to confirm the server is the current build."""
    return jsonify({"ok": True, "has_generate": True})


def main() -> None:
    port = 8777
    url = f"http://127.0.0.1:{port}"
    print(f"Weapon editor  ->  {url}")
    print("Ctrl+C to stop")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
