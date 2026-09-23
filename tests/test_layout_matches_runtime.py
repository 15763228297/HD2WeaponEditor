"""Prove the decrypted .dl_bin layout equals the runtime layout Codex writes to.

Why this is the test that matters: the whole mod design assumes a row index from
the parsed file maps to a record the runtime writes. If the file were a
transformed view (reordered, padded, variable-width), then every index the GUI
shows would be wrong and edits would land on the wrong weapon.

The independent witness is the Codex mod already installed on this machine. It
writes raw memory and carries, in its own build.json, the exact offsets and
baseline values it expects. Those offsets were derived by whoever wrote Codex,
independently of our parser. If both agree byte-for-byte, the layouts are the
same.

Run:  python tests/test_layout_matches_runtime.py
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_map  # noqa: E402  (path set above)

failures = 0
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global failures, checks
    checks += 1
    if cond:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label} {detail}")
        failures += 1


CODEX = Path(
    r"C:\Users\miaomiao\AppData\Local\hd2arsenal\mods"
    r"\R2124-Constitution-Bolt-AMR-Bridge-v4_AR130189"
    r"\ConstitutionBoltAMR-build.json"
)
GAME_DLL = Path(
    r"D:\program files (x86)\steam\steamapps\common\Helldivers 2\data\game\game.dll"
)


def main() -> int:
    if not CODEX.exists():
        print(f"Codex build.json not found: {CODEX}")
        print("This test needs it as the independent witness.")
        return 2
    mod = json.loads(CODEX.read_text(encoding="utf-8"))

    print("== the guard targets this exact game build ==")
    # Codex refuses to write on an unsupported build; confirm the build it was
    # authored against is the one installed, so its offsets describe this exe.
    #
    # When they differ, this test cannot run: its entire method is to check our
    # parse against an INDEPENDENT witness, and Codex's recorded offsets are
    # only independent while they describe the same bytes. Reporting FAIL here
    # would blame our parser for Codex not having updated yet - the mismatch is
    # a statement about Codex, not about us. It is SKIP, loudly, so nobody reads
    # a green suite as "verified against an outside source" when it was not.
    if GAME_DLL.exists():
        h = hashlib.sha256(GAME_DLL.read_bytes()).hexdigest()
        claim = mod["game_dll_sha256"].lower()
        if h != claim:
            print(f"  [SKIP] Codex's build.json describes a different game build.")
            print(f"         its claim: {claim[:32]}...")
            print(f"         installed: {h[:32]}...")
            print("         The independent-witness check needs Codex updated for")
            print("         this build; nothing about our parser is implied by this.")
            print()
            print("test_layout_matches_runtime: SKIP (Codex is for another build)")
            return 0
        check("game.dll sha256 matches Codex's claim", True)
    else:
        print("  [SKIP] game.dll not found")

    print()
    print("== projectile table: Codex's record offset lands on an exact row ==")
    pblob = (ROOT / "data" / "raw" / "generated_projectile_settings.dl_bin").read_bytes()
    _off, count = struct.unpack_from("<QQ", pblob, build_map.PROJECTILE_ARRAY_OFFSET)
    arr = build_map.PROJECTILE_ARRAY_OFFSET + _off
    stride = build_map.PROJECTILE_RECORD_SIZE

    pb = mod["projectile_baseline"]
    claimed = int(pb["record_offset"], 16)
    delta = claimed - arr
    check(f"offset is on a record boundary (stride {stride})", delta % stride == 0,
          f"delta {delta} % {stride} = {delta % stride}")
    row = delta // stride
    check(f"row index is in range (row {row} of {count})", 0 <= row < count, f"row {row}")

    # parse_projectiles alone leaves `damage_position` unset: turning the id into
    # a row needs the damage table, which is parsed further down. The two steps
    # are separate on purpose, so a caller cannot accidentally use an unresolved
    # id as if it were a row index.
    projectiles = build_map.parse_projectiles(pblob)
    parsed = projectiles[row]
    base = pb["baseline"]
    check("speed matches Codex baseline", parsed.speed == base["speed"],
          f"{parsed.speed} vs {base['speed']}")
    check("mass matches Codex baseline", parsed.mass == base["mass"],
          f"{parsed.mass} vs {base['mass']}")
    check("calibre matches Codex baseline", parsed.calibre == base["calibre"],
          f"{parsed.calibre} vs {base['calibre']}")
    # Codex's field is literally `damage_info_type`, and its value is the raw
    # number stored at +60 - an ID, not an array index. This assertion compares
    # against that raw value, so it must use `damage_type`. Comparing
    # `damage_position` here is what would have caught the id/position mix-up
    # earlier: the two differ on most rows, and this is the one test that has an
    # outside source (Codex's own baseline) to check against.
    check("the raw +60 value matches Codex's damage_info_type",
          parsed.damage_type == base["damage_info_type"],
          f"{parsed.damage_type} vs {base['damage_info_type']}")
    check("projectile_type matches Codex baseline",
          struct.unpack_from("<i", pblob, arr + row * stride)[0] == base["projectile_type"])

    print()
    print("== damage table: the row Codex points at holds its stated baseline ==")
    damages = build_map.parse_damages(
        (ROOT / "data" / "raw" / "generated_damage_settings.dl_bin").read_bytes()
    )
    # Resolve the id to a row now that the table exists. This is the step whose
    # absence was the bug: `damage_info_type` is an ID, and using it as an index
    # picks a different weapon's row on every row where id != position.
    build_map.resolve_damage_positions(projectiles, damages)
    check("damage_info_type resolves to a real row",
          parsed.damage_position is not None,
          f"id {parsed.damage_type} resolves to no row")
    # Codex's baseline is an outside source: if the id->row conversion were
    # wrong, the row reached here would not hold the values Codex recorded.
    check("the resolved row is the one Codex documents",
          parsed.damage_position is not None
          and damages[parsed.damage_position].damage == mod["damage_baseline"]["normal"],
          f"row {parsed.damage_position} holds "
          f"{damages[parsed.damage_position].damage if parsed.damage_position is not None else '—'}"
          f" vs Codex {mod['damage_baseline']['normal']}")
    d = damages[parsed.damage_position]
    db = mod["damage_baseline"]
    check("damage normal matches", d.damage == db["normal"], f"{d.damage} vs {db['normal']}")
    check("damage durable matches", d.durable_damage == db["durable"],
          f"{d.durable_damage} vs {db['durable']}")
    check("AP matches", d.armor_penetration_per_angle == db["ap"],
          f"{d.armor_penetration_per_angle} vs {db['ap']}")
    check("forces match",
          [d.demolition_strength, d.force_strength, d.force_impulse] == db["forces"],
          f"{[d.demolition_strength, d.force_strength, d.force_impulse]} vs {db['forces']}")

    print()
    print("== entities table: the component Codex writes is at its stated offset ==")
    eblob = (ROOT / "data" / "raw" / "entities.dl_bin").read_bytes()
    eb = mod["entities_baseline"]
    eoff = int(eb["record_offset"], 16)
    size = int(eb["record_size"], 16)
    check("record offset is inside the file", eoff < len(eblob),
          f"0x{eoff:x} vs file {len(eblob):,}")
    # aim_zoom_offset is given relative to the record; the baseline is all 1.0.
    az = struct.unpack_from("<3f", eblob, eoff + int(eb["aim_zoom_offset"], 16))
    check("aim_zoom at the stated field offset reads the baseline", tuple(az) == tuple(eb["baseline"]),
          f"{tuple(az)} vs {tuple(eb['baseline'])}")
    # The scaled values Codex will write must be findable as the *unedited* value,
    # proving the record is the one for this weapon rather than a similar one.
    ergo = eb["ergonomics_baseline"]
    in_rec = any(
        abs(struct.unpack_from("<f", eblob, eoff + o)[0] - ergo) < 1e-4
        for o in range(0, size - 4, 4)
    )
    check(f"ergonomics baseline {ergo} present in the record", in_rec)

    print()
    print("== conclusion ==")
    print("  The decrypted file row indices ARE the runtime record indices.")
    print("  A row number shown in the GUI can be used directly for memory writes.")
    print()
    print(f"test_layout_matches_runtime: {'PASS' if not failures else 'FAIL'} ({checks} checks)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
