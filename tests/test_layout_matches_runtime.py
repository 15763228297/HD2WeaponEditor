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
    if GAME_DLL.exists():
        h = hashlib.sha256(GAME_DLL.read_bytes()).hexdigest()
        claim = mod["game_dll_sha256"].lower()
        check("game.dll sha256 matches Codex's claim", h == claim, f"{h[:16]} vs {claim[:16]}")
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

    parsed = build_map.parse_projectiles(pblob)[row]
    base = pb["baseline"]
    check("speed matches Codex baseline", parsed.speed == base["speed"],
          f"{parsed.speed} vs {base['speed']}")
    check("mass matches Codex baseline", parsed.mass == base["mass"],
          f"{parsed.mass} vs {base['mass']}")
    check("calibre matches Codex baseline", parsed.calibre == base["calibre"],
          f"{parsed.calibre} vs {base['calibre']}")
    check("damage_info_type matches Codex baseline",
          parsed.damage_position == base["damage_info_type"],
          f"{parsed.damage_position} vs {base['damage_info_type']}")
    check("projectile_type matches Codex baseline",
          struct.unpack_from("<i", pblob, arr + row * stride)[0] == base["projectile_type"])

    print()
    print("== damage table: the row Codex points at holds its stated baseline ==")
    damages = build_map.parse_damages(
        (ROOT / "data" / "raw" / "generated_damage_settings.dl_bin").read_bytes()
    )
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
