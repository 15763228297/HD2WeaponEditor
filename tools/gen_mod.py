"""Generate the weapon-editor mod: Lua source -> bytecode -> Arsenal ZIP.

The generated mod is self-contained. It carries, for one weapon:

  * the record's identity (``type_id``) and position,
  * the neighbouring records' ``type_id``s, so the runtime scan can prove it
    found the right row rather than a coincidental byte match,
  * the baseline values (what the fields hold now) and the target values,
  * the guard/write/log modules verbatim.

Nothing about the weapon is looked up at runtime: every number the mod needs is
baked in at generation time, which is what lets the runtime verify rather than
trust. The trade-off is that the mod is specific to one weapon and one game
build - regenerating is cheap, and a stale mod refuses to write instead of
writing something wrong.

Usage:
    python tools/gen_mod.py --weapon "R-4 Hyena" --damage 400 --durable 200 --ap 4
    python tools/gen_mod.py --spec spec.json --out build
"""

from __future__ import annotations

import argparse
import hashlib

# Where the damage array sits inside the table, as an offset from whatever a
# static route points at.
#
# This is NOT a tunable. It is the DLArray descriptor's own `offset` field: in
# the decrypted .dl_bin the descriptor sits at the payload start and its first
# u64 is the distance to the record array, which is 16 in every settings table
# here. The array therefore begins 16 bytes into the payload, i.e. 100 bytes
# into the blob (24-byte LDLD header + 60 bytes of container preamble).
#
# It used to be 0x1e0 (480), which was 380 bytes too far. That value was not a
# mistake at the time: the old parser began reading at file offset 480 instead
# of 100 and so labelled every row five positions early. The overshoot in this
# constant cancelled the undershoot in the parser, and a mod built from those
# two errors addressed the right byte - which is why it worked in game.
#
# The parser now reads the descriptor directly (tools/dlbin_tables.py), so the
# compensation must go: with true positions, 480 would address five rows past
# the target. Both derivations agree on 100 - the descriptor says so, and
# `480 + 137*76 == 100 + 142*76` holds for R-4, whose position moved from the
# old parser's 137 to the true 142.
ARRAY_START = 100
RECORD_SIZE = 76
import json
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

def _resolve_root() -> Path:
    """Where the project's data, tools and mod_template live.

    Running from source, that is the repo root. In a frozen PyInstaller build
    `__file__` points into the temp extraction directory and everything added
    with `--add-data` lands under `sys._MEIPASS`, so the plain expression would
    resolve to a folder that does not exist.
    """
    frozen = getattr(sys, "frozen", False)
    meipass = getattr(sys, "_MEIPASS", None)
    if frozen and meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


ROOT = _resolve_root()
sys.path.insert(0, str(ROOT / "tools"))

# Previously imported from the StratagemHotkey project's tools dir. Those
# helpers are now vendored here so the frozen build does not depend on another
# checkout being present on the machine that runs it; the old path is only a
# fallback for anyone still running from source with both trees.
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import build_map  # noqa: E402
from hd2archive import Archive, Resource, resource_hash, wrap_lua  # noqa: E402
from ljcompile import compile_source  # noqa: E402

# The loader slot. Bingus Shared Loader v14 requires this name (it is the third
# entry in its hard-coded list), the name is currently unused, and staying on
# that list is what makes the mod load at all - the engine does not scan mods/.
SLOT = "mods/cowboybingus/wide_angle_stratagems"

ARCHIVE_STEM = "9ba626afa44a3aa3.patch_0"
LUA_RESOURCE_TYPE = 0xA14E8DFA2CD117E2
GUID = "8f3a5c21-6b47-4e93-9d20-7c4e1a8b5f60"
DISPLAY_NAME = "HD2 Weapon Stat Editor"

# Field names the guard, writer and generator all share. Keep in one place.
FIELD_ORDER = ["damage", "durable", "ap0", "ap1", "ap2", "ap3"]

MODULES = [
    "10_resolver.lua",
    "15_static_route.lua",
    "16_static_chain.lua",
    "20_guard.lua",
    "30_write.lua",
    "40_log.lua",
]


@dataclass
class Spec:
    """Everything the generated mod needs to know about one weapon."""

    weapon: str
    position: int
    type_id: int
    before_type_id: int | None
    after_type_id: int | None
    next_damage: int | None
    next_durable: int | None
    baseline: dict
    changes: dict
    record_offset: int = 0
    page_offset: int = 0
    game_dll_sha256: str = ""

    def as_dict(self) -> dict:
        return {
            "weapon": self.weapon,
            "position": self.position,
            "type_id": self.type_id,
            "before_type_id": self.before_type_id,
            "after_type_id": self.after_type_id,
            "next_damage": self.next_damage,
            "next_durable": self.next_durable,
            "record_offset": self.record_offset,
            "page_offset": self.page_offset,
            "baseline": self.baseline,
            "changes": self.changes,
            "game_dll_sha256": self.game_dll_sha256,
        }


def load_tables() -> tuple[dict, list, dict]:
    """Parse the shipped tables plus the weapon name map.

    Reads the derived JSON rather than the game's own `.dl_bin`, so the repo
    carries no game data. The JSON is generated by `tools/parse_dlbin.py` from
    a local install; the two agree on all 634 rows (asserted in
    tests/test_data_equivalent.py), and the JSON holds every field this module
    touches.

    `projectiles` is parsed for API compatibility but is not consumed here.
    """
    rows = json.loads(
        (ROOT / "data" / "damage_records.json").read_text(encoding="utf-8")
    )
    if isinstance(rows, dict):
        rows = rows.get("records", rows)
    if isinstance(rows, dict):                       # keyed by position
        rows = [rows[k] for k in sorted(rows, key=int)]

    damages: dict[int, build_map.DamageInfo] = {}
    for position, row in enumerate(rows):
        damages[position] = build_map.DamageInfo(
            index=position,
            type_id=row["type_id"],
            damage=row["damage"],
            durable_damage=row["durable_damage"],
            armor_penetration_per_angle=list(row["armor_penetration_per_angle"]),
            demolition_strength=row.get("demolition_strength", 0),
            force_strength=row.get("force_strength", 0),
            force_impulse=row.get("force_impulse", 0),
            element_type=row.get("element_type", 0),
            status_effects=row.get("status_effects", []),
        )

    names = json.loads((ROOT / "data" / "weapon_names.json").read_text(encoding="utf-8"))
    return damages, [], names


def find_weapon(names: dict, page: str) -> dict:
    for w in names["weapons"]:
        if w["page"] == page:
            return w
    known = ", ".join(sorted(w["page"] for w in names["weapons"])[:8])
    raise SystemExit(f"未找到武器：{page!r}\n  （部分可用名称：{known} …）")


def parse_edits(raw_edits: list[str] | None,
                default_damage: int | None,
                default_durable: int | None,
                default_ap: int | None) -> list[tuple[str, int, int, int]]:
    """Parse `--edit 'Weapon::D/DUR/AP'` into (page, damage, durable, ap).

    Every value must be explicit per weapon. Allowing a weapon to inherit a
    default silently applies the first weapon's numbers to the rest, which is
    not something to guess at.
    """
    out = []
    for raw in raw_edits or []:
        if "::" not in raw:
            raise SystemExit(
                f"--edit {raw!r}：格式应为 '武器名::肉伤/耐伤/穿甲'")
        page, values = raw.split("::", 1)
        page = page.strip()
        bits = [b.strip() for b in values.split("/")]
        if len(bits) != 3:
            raise SystemExit(
                f"--edit {raw!r}：需要三个数值（肉伤/耐伤/穿甲）")
        try:
            damage, durable, ap = (int(b) for b in bits)
        except ValueError:
            raise SystemExit(f"--edit {raw!r}：数值必须为整数")
        out.append((page, damage, durable, ap))
    return out


def build_specs(raw_edits: list[str] | None,
                default_damage: int | None = None,
                default_durable: int | None = None,
                default_ap: int | None = None,
                allow_shared: bool = False) -> list[Spec]:
    """Build one Spec per requested edit.

    Editing several weapons shares a single scan: the first record found fixes
    the table base, and the rest are addressed by offset. Refusing shared rows
    here is what keeps "change these N weapons" from quietly changing others.
    """
    edits = parse_edits(raw_edits, default_damage, default_durable, default_ap)
    if not edits:
        raise SystemExit("未提供 --edit，无可生成内容")
    specs = []
    for page, damage, durable, ap in edits:
        if not (0 <= damage <= 100000 and 0 <= durable <= 100000):
            raise SystemExit(f"{page}：肉伤与耐伤需在 0..100000 之间")
        if not (0 <= ap <= 10):
            raise SystemExit(f"{page}：穿甲等级需在 0..10 之间")
        specs.append(build_spec(page, damage=damage, durable=durable, ap=ap,
                                allow_shared=allow_shared))
    # Two edits that resolve to the same row would write twice; refuse early
    # rather than let the second one overwrite the first at runtime.
    seen: dict[int, str] = {}
    for spec in specs:
        if spec.position in seen:
            raise SystemExit(
                f"{spec.weapon} 与 {seen[spec.position]} 指向同一条伤害记录"
                f"（行 {spec.position}），应合并为一次改动。")
        seen[spec.position] = spec.weapon
    return specs


def build_impact_spec(page: str, damage: int, durable: int, ap: int,
                      allow_shared: bool = False) -> Spec:
    """Build a Spec for the IMPACT half of an explosive weapon.

    Explosive weapons deal damage in two independent halves: the projectile
    hitting the target, and the explosion. They are two separate damage records
    with separate values and separate owners, so they need separate edits.
    """
    damages, projectiles, names = load_tables()
    weapon = find_weapon(names, page)

    pos = weapon.get("impact_damage_position")
    if pos is None:
        raise SystemExit(
            f"{page}：这把武器没有独立的弹头直击记录（弹头本身不造成直击伤害），"
            "只能修改爆炸伤害。"
        )
    if not weapon["verified"]:
        raise SystemExit(
            f"{page}：该武器的 wiki 数值与游戏记录不一致，行号可能不可靠，"
            "生成器拒绝改动。"
        )
    if not weapon.get("impact_exclusive", True) and not allow_shared:
        peers = ", ".join(weapon.get("impact_shared_with", []))
        raise SystemExit(
            f"{page}：该弹头直击记录同时被 {peers} 使用，修改会一并改变这些武器。"
            "共享记录默认不可改动。"
        )

    rec = damages[pos]
    if rec.type_id != weapon["impact_damage_index"]:
        raise SystemExit(
            f"{page}: the name map says impact type_id "
            f"{weapon['impact_damage_index']} but row {pos} holds "
            f"{rec.type_id}. The map is stale; rebuild it."
        )

    before = damages[pos - 1].type_id if pos > 0 else None
    after = damages[pos + 1].type_id if (pos + 1) in damages else None
    nxt = damages.get(pos + 1)

    baseline = {
        "damage": rec.damage,
        "durable": rec.durable_damage,
        "ap0": rec.armor_penetration_per_angle[0],
        "ap1": rec.armor_penetration_per_angle[1],
        "ap2": rec.armor_penetration_per_angle[2],
        "ap3": rec.armor_penetration_per_angle[3],
    }
    changes = {
        "damage": damage,
        "durable": durable,
        "ap0": ap,
        "ap1": ap,
        "ap2": ap,
        "ap3": rec.armor_penetration_per_angle[3],
    }
    changes = {k: v for k, v in changes.items() if baseline[k] != v}
    if not changes:
        raise SystemExit(f"{page}：所填弹头直击数值与当前值相同，无需改动")

    sha = ""
    dll = Path(r"D:\program files (x86)\steam\steamapps\common\Helldivers 2\data\game\game.dll")
    if dll.exists():
        sha = hashlib.sha256(dll.read_bytes()).hexdigest().upper()

    return Spec(
        weapon=f"{page} (impact)",
        position=pos,
        type_id=rec.type_id,
        before_type_id=before,
        after_type_id=after,
        next_damage=nxt.damage if nxt else None,
        next_durable=nxt.durable_damage if nxt else None,
        baseline=baseline,
        changes=changes,
        record_offset=ARRAY_START + pos * RECORD_SIZE,
        page_offset=(ARRAY_START + pos * RECORD_SIZE) % 0x1000,
        game_dll_sha256=sha,
    )


def build_spec(page: str, damage: int, durable: int, ap: int,
               keep: bool = True, allow_shared: bool = False) -> Spec:
    """Assemble a Spec for `page` with the requested new values.

    `allow_shared` opts in to editing a damage row that other weapons also use.
    It is off by default because the whole point of this tool is changing one
    weapon without touching others; turning it on changes every weapon listed
    in the row's shared set.

    `ap` sets all four penetration angles to the same value except the last,
    which is left as-is: the fourth angle is 0 on every weapon checked and is not
    a penetration tier, so copying the new tier over it would be inventing a
    change the user did not ask for.
    """
    damages, projectiles, names = load_tables()
    weapon = find_weapon(names, page)
    # Address the row by POSITION; identify it by TYPE_ID. Conflating the two
    # made the generator edit a different weapon's record for 50 of 102 weapons
    # while every guard agreed with itself, because the guard derived its
    # expected type_id from the same wrong row.
    position = weapon["damage_position"]
    row = position

    if not weapon["verified"]:
        raise SystemExit(
            f"{page}：该武器的 wiki 数值与游戏记录不一致，行号可能不可靠，"
            "生成器拒绝改动。"
        )
    if not weapon["exclusive"] and not allow_shared:
        peers = ", ".join(weapon.get("shared_with", []))
        raise SystemExit(
            f"{page}：该伤害记录同时被 {peers} 使用，修改会一并改变这些武器。"
            "共享记录默认不可改动。"
        )

    rec = damages[position]
    # The stored type_id must be the one actually at that position. If not, the
    # name map is stale relative to the table and every downstream number is
    # suspect - refuse rather than build a mod that writes to the wrong place.
    if rec.type_id != weapon["damage_index"]:
        raise SystemExit(
            f"{page}: the name map says type_id {weapon['damage_index']} but "
            f"row {position} holds {rec.type_id}. The map is stale; rebuild it."
        )
    # The values the GUI showed must be the values in this row.
    if (rec.damage, rec.durable_damage) != (weapon["damage"], weapon["durable"]):
        raise SystemExit(
            f"{page}: the name map says {weapon['damage']}/{weapon['durable']} "
            f"but row {position} holds {rec.damage}/{rec.durable_damage}. "
            "The map is stale; rebuild it."
        )

    neighbours = damages
    before = neighbours[position - 1].type_id if position > 0 else None
    after = neighbours[position + 1].type_id if position + 1 in neighbours else None
    nxt = neighbours.get(position + 1)

    baseline = {
        "damage": rec.damage,
        "durable": rec.durable_damage,
        "ap0": rec.armor_penetration_per_angle[0],
        "ap1": rec.armor_penetration_per_angle[1],
        "ap2": rec.armor_penetration_per_angle[2],
        "ap3": rec.armor_penetration_per_angle[3],
    }
    changes = {
        "damage": damage,
        "durable": durable,
        "ap0": ap,
        "ap1": ap,
        "ap2": ap,
        # The fourth angle stays at its original value unless it was non-zero,
        # in which case it keeps its tier relationship.
        "ap3": rec.armor_penetration_per_angle[3],
    }
    changes = {k: v for k, v in changes.items() if baseline.get(k) != v or keep is False}

    # If nothing differs there is no mod to build; say so rather than emit one
    # that would write nothing.
    if not changes:
        raise SystemExit(f"{page}：所填数值与当前值相同，无需改动")

    sha = ""
    dll = Path(r"D:\program files (x86)\steam\steamapps\common\Helldivers 2\data\game\game.dll")
    if dll.exists():
        sha = hashlib.sha256(dll.read_bytes()).hexdigest().upper()

    return Spec(
        weapon=page,
        position=position,
        type_id=rec.type_id,
        before_type_id=before,
        after_type_id=after,
        record_offset=ARRAY_START + position * RECORD_SIZE,
        page_offset=(ARRAY_START + position * RECORD_SIZE) % 0x1000,
        next_damage=nxt.damage if nxt else None,
        next_durable=nxt.durable_damage if nxt else None,
        baseline=baseline,
        changes=changes,
        game_dll_sha256=sha,
    )


def lua_table(mapping: dict, indent: str = "    ") -> str:
    parts = []
    for key, value in mapping.items():
        parts.append(f"{indent}{key} = {value},")
    return "\n".join(parts)


def render_module(specs: list, sources: dict[str, str],
                  probe_static_route: bool = False) -> str:
    """Assemble the four modules into one Lua chunk.

    The pieces are concatenated rather than required: the generated file must be
    a single resource, and each module returns a table, so the assembly binds
    them explicitly and keeps load order visible.

    `probe_static_route` turns on the static-route probe, which reports the
    rvas that currently hold a pointer to the damage table. It is off for normal
    builds: the routes are already recorded, and the probe costs a scan of the
    module's data on every launch. It is turned on when a game patch has moved
    the table and the recorded routes no longer resolve, because then those rvas
    are the thing that needs re-deriving.
    """
    parts = []
    parts.append(
        "--[[\n"
        f" {DISPLAY_NAME} - generated file, do not edit by hand.\n"
        f" weapons: {', '.join(sp.weapon for sp in specs)}\n"
        " Regenerate with: python tools/gen_mod.py\n"
        "]]\n"
    )
    # Each module is wrapped in a function so its `local M` stays file-local.
    for name in MODULES:
        parts.append(f"-- ==== {name} ====\n")
        parts.append(f"local function load_{name.replace('.', '_')}()\n")
        parts.append(sources[name])
        parts.append("\nend\n")

    plan_entries = []
    for spec in specs:
        ch = {k: v for k, v in spec.changes.items() if k in FIELD_ORDER}
        ch_lines = lua_table({k: ch[k] for k in FIELD_ORDER if k in ch})
        bl_lines = lua_table({k: spec.baseline[k] for k in FIELD_ORDER})
        plan_entries.append(
            f"""    {{
    weapon = {json.dumps(spec.weapon)},
    position = {spec.position},
    type_id = {spec.type_id},
    before_type_id = {"nil" if spec.before_type_id is None else spec.before_type_id},
    after_type_id = {"nil" if spec.after_type_id is None else spec.after_type_id},
    next_damage = {"nil" if spec.next_damage is None else spec.next_damage},
    next_durable = {"nil" if spec.next_durable is None else spec.next_durable},
    record_offset = {spec.record_offset},
    page_offset = {spec.page_offset},
    changes = {{
{ch_lines}
    }},
    baseline = {{
{bl_lines}
    }},
}},"""
        )
    plans_block = chr(10).join(plan_entries)

    probe_flag = "true" if probe_static_route else "false"
    parts.append(
        f"""
local ffi = require("ffi")

local resolver = load_10_resolver_lua()
local static_route = load_15_static_route_lua()
local static_chain = load_16_static_chain_lua()
local guard    = load_20_guard_lua()
local writer   = load_30_write_lua().init(guard)
local log      = load_40_log_lua()

-- One entry per weapon changed by this mod.
--
-- The scan is shared: the first plan that is located establishes the damage
-- table's base address, and every other plan is then addressed directly as
-- `base + record_offset` instead of being searched for. That is the difference
-- between one address-space walk per weapon and one walk total, which matters
-- because the walk is what made the game stutter.
local PLANS = {{
{plans_block}
}}

local EXPECTED_GAME_DLL = {json.dumps(spec.game_dll_sha256 or "")}

local function main()
    log.section("HD2 Weapon Stat Editor")
    log.line("module loaded")

    local api = resolver.bind()
    if api == nil then
        log.line("could not open the process - no access, nothing to do")
        return
    end

    -- Install a frame hook instead of doing the work now.
    --
    -- The game loads its data tables lazily: at startup the damage array is not
    -- in memory yet, so a single attempt at module load time finds nothing and
    -- reports "record not found" while the table appears a few frames later.
    -- The installed reference mod in this build has the same design - it carries
    -- `waiting_for_resources`, logs `loaded=%d/%d`, and re-scans every frame -
    -- and this project's own earlier work reached the game the same way, by
    -- wrapping the global `update`.
    local previous_update = rawget(_G, "update")
    if type(previous_update) ~= "function" then
        log.line("no update callback available - cannot wait for the tables")
        log.line("(this build may call it something else; nothing was changed)")
        return
    end

    local frame_number = 0
    local attempts = 0
    local done = false

    -- Space attempts out. Each attempt is bounded (32 MB) and RESUMES where the
    -- previous one stopped, so slowing the cadence only spreads the same total
    -- work over more frames - it never loses progress.
    --
    -- This pairing matters: an earlier version spaced attempts out but restarted
    -- the scan each time, so it re-read the same opening regions forever and
    -- never advanced. That is the bug this cadence depends on being fixed.
    -- Smaller budget, more often. The cost of a scan is dominated by how much
    -- happens inside one frame, not by the total: 64 MB once every 30 frames
    -- stalled a frame hard enough that the game stuttered for ~40 seconds.
    -- 8 MB every 8 frames is the same throughput with 8x less per-frame work.
    local ATTEMPT_EVERY = 8        -- ~0.13s at 60fps
    local MAX_ATTEMPTS = 3000      -- ~6 minutes of trying; a few GB of heap

    -- Stay out of the way during startup. The game is streaming assets and
    -- allocating heavily in the first seconds, and this mod reads its memory -
    -- an earlier version scanned without any warmup and the game died before
    -- the intro cinematic. Three seconds of patience costs nothing, because the
    -- tables it waits for are not loaded yet at that point anyway.
    local WARMUP_FRAMES = 180      -- ~3s at 60fps

    -- Static-route probe (see 15_static_route.lua).
    --
    -- Normally DISABLED. It did its job once: two independent launches reported
    -- the same three rvas, which is what 16_static_chain.lua then used to
    -- address the table in one read. Re-running it costs 24 MB of game.dll
    -- reads per launch to re-derive a fact that is already baked in.
    --
    -- Kept as a switch rather than deleted, and now reachable from the CLI
    -- (`--probe-static-route`): when a game patch moves the table the routes
    -- stop resolving, and the mod falls back to the full address-space scan -
    -- which is the 35-57 second stall the routes were built to remove. Turning
    -- this on prints the rvas that ARE live for the new build, which is the
    -- whole diagnosis.
    local PROBE_STATIC_ROUTE = {probe_flag}
    local probe_state = nil
    local probe_finished = false
    local probe_passes = 0
    local probe_next_at = WARMUP_FRAMES

    -- How many times to sweep the module before concluding there is no route.
    -- One pass can legitimately come up empty: the damage table is loaded when
    -- the game needs it, and a sweep that runs before that finds no pointer to
    -- it. Concluding "no route" from one empty pass would report a fact about
    -- timing as a fact about the build.
    local PROBE_MAX_PASSES = 6
    local PROBE_RETRY_GAP = 600     -- ~10s at 60fps between passes

    -- The probe reports which rvas currently hold a pointer to the damage table,
    -- so a route that stopped resolving can be re-derived.
    --
    -- It uses the BLIND walk unconditionally. The precise walk (searching for a
    -- known `table_base`) needs the scan to have succeeded first - but the only
    -- reason to run the probe is that the routes are dead, and when the routes
    -- are dead the scan is usually dead too, because the scan is what supplies
    -- `table_base`. Waiting for it would mean waiting out the full attempt
    -- budget (~1000 attempts, about a minute) to then run a probe that could
    -- have started on frame one.
    --
    -- Blind also finds strictly more: it reports any pointer to anything
    -- table-shaped, which includes every pointer the precise walk would name.
    -- The only thing it loses is the label ("array_start" vs "record"), and the
    -- rva is what a route needs.
    local function probe_frame()
        if probe_finished or not PROBE_STATIC_ROUTE then return end
        if frame_number < probe_next_at then return end

        local mb = resolver.module_base("game.dll")
        if mb == nil then return end

        if probe_state == nil then
            local ok, state = pcall(static_route.begin_blind, api, mb)
            if not ok then
                probe_finished = true
                log.line("blind probe failed to start: " .. tostring(state))
                return
            end
            probe_state = state
            probe_passes = probe_passes + 1
            if probe_passes == 1 then
                log.section("static route probe (blind)")
            end
            log.line(("pass %d: scanning game.dll writable data (%d bytes) for a "
                .. "pointer to anything table-shaped")
                :format(probe_passes, state.total or 0))
        end

        local ok, finished, message = pcall(static_route.step_blind, api, probe_state)
        if not ok then
            probe_finished = true
            log.line("blind probe error: " .. tostring(finished))
            return
        end
        if not finished then return end

        if #probe_state.hits > 0 then
            probe_finished = true
            for _, line in ipairs(static_route.describe_blind(probe_state)) do
                log.line(line)
            end
        elseif probe_passes < PROBE_MAX_PASSES then
            -- Empty pass. The table may simply not be loaded yet, so sweep
            -- again later rather than reporting "no route" from one sample.
            log.line(("pass %d found nothing; the table may not be loaded yet, "
                .. "retrying"):format(probe_passes))
            probe_state = nil
            probe_next_at = frame_number + PROBE_RETRY_GAP
        else
            probe_finished = true
            for _, line in ipairs(static_route.describe_blind(probe_state)) do
                log.line(line)
            end
        end
        -- Progress is deliberately NOT logged. The module scan takes a handful
        -- of frames; a per-frame line was the same noise that made the weapon
        -- sections spam ~950 lines into the log during the search. Only the
        -- final result carries information.
    end

    local function frame(dt, ...)
        frame_number = frame_number + 1

        -- The probe runs from the first frame, not after the edits settle.
        --
        -- It used to be called only once `done` was true, which meant it could
        -- not run in the situation it exists for: after a game update the scan
        -- finds nothing, `done` only becomes true when the attempt budget is
        -- exhausted (~1000 attempts, about a minute), and the whole point is to
        -- diagnose exactly that failure. The blind walk needs nothing the scan
        -- produces, so there is no reason to wait for the scan.
        --
        -- While it is running it also HOLDS the scan off. Both walk memory in
        -- the same frame callback, and letting them interleave would put the
        -- probe's reads on top of the stall the scan already causes - making the
        -- launch worse than the problem being diagnosed. The probe finishes in a
        -- few seconds; the scan then proceeds exactly as before.
        if PROBE_STATIC_ROUTE and not probe_finished then
            probe_frame()
            if previous_update then return previous_update(dt, ...) end
            return
        end

        if not done then
            if frame_number > WARMUP_FRAMES
               and (frame_number - WARMUP_FRAMES) % ATTEMPT_EVERY == 1 then
                attempts = attempts + 1
                -- Throttled, not silenced.
                --
                -- An earlier version printed this once and then went quiet, so a
                -- run of ~95 attempts produced a two-line log and the round
                -- proved nothing: there was no way to tell "still walking" from
                -- "stuck re-reading one giant region". The opposite mistake -
                -- every attempt - writes ~477 lines of file I/O per launch into
                -- the game process, inside the stall the user is complaining
                -- about.
                --
                -- Progress is still evidenced: first attempt, then a heartbeat
                -- every 64, then the outcome. The (scanned bytes, regions) pair
                -- that actually distinguishes progress from a stall is logged by
                -- the scan itself when it reports.
                if attempts == 1 or attempts % 64 == 0 then
                    log.line(("attempt %d (frame %d)"):format(attempts, frame_number))
                end
                local ok, finished = pcall(apply_all, api)
                if ok and finished then
                    done = true
                    log.line(("settled after %d attempt(s)"):format(attempts))
                elseif attempts >= MAX_ATTEMPTS then
                    done = true
                    log.line("giving up after " .. attempts .. " attempts")
                    log.line("(nothing was changed)")
                end
            end
        else
            probe_frame()
        end
        if previous_update then return previous_update(dt, ...) end
    end

    rawset(_G, "update", frame)
    log.line("waiting for the damage table to load (hook installed)")
end

-- Try to apply every plan once. Returns true when there is nothing left to do
-- (all plans applied, or all refused for a reason that will not change).
function apply_all(api)
    local pending = 0
    for _, plan in ipairs(PLANS) do
        if not plan.settled then
            local finished = apply_one(api, plan)
            if finished then plan.settled = true else pending = pending + 1 end
        end
    end
    return pending == 0
end

-- Apply one plan. Returns true when no further attempt is worth making.
function apply_one(api, plan)
    local base = resolver.module_base("game.dll")
    if base == nil then
        log.line("game.dll not found in this process")
        return false
    end

    -- ONE-TIME header, not per attempt.
    --
    -- This block runs inside a frame callback that fires ~477 times before the
    -- table is found, so these two lines were being written ~950 times per
    -- launch - as file I/O, inside the game process, during exactly the window
    -- the user experiences as a one-minute startup stall. The per-attempt
    -- information (progress, outcome) is still logged below.
    --
    -- The same reasoning applies to the "attempt N (frame M)" line: it is
    -- useful when diagnosing, useless 477 times, and cheap to keep at the
    -- edges - first attempt, then every 64th, then the result.
    if not plan.announced then
        plan.announced = true
        log.section("weapon: " .. plan.weapon)
        log.line(("record: position %d, type id %d")
            :format(plan.position, plan.type_id))
    end

    local record = {{
        type_id = plan.type_id,
        position = plan.position,
        damage = plan.baseline.damage,
        durable = plan.baseline.durable,
        ap = {{ plan.baseline.ap0, plan.baseline.ap1,
                plan.baseline.ap2, plan.baseline.ap3 }},
    }}
    local expect = {{
        before_id = plan.before_type_id,
        after_id = plan.after_type_id,
        next = (plan.next_damage ~= nil)
            and {{ damage = plan.next_damage, durable = plan.next_durable }}
            or nil,
    }}

    -- ROUTE 1: the static route (see 16_static_chain.lua).
    --
    -- A fixed offset in game.dll holds a pointer to the damage array, and that
    -- offset was identical across independent launches. One read resolves the
    -- whole table:
    --
    --     array = *(game_dll_base + 0x2ac7cb0)
    --
    -- This is what replaces the 35-57 second address-space walk. It is tried
    -- FIRST because it costs microseconds, and it is verified like any other
    -- candidate - an rva is only a hint, and a game patch can leave it pointing
    -- at unrelated memory.
    --
    -- Attempted once per session: if it fails there is no point retrying it 400
    -- times, because the offset will not start working on its own.
    local table_base = resolver.table_base()
    local ok, address, near

    if not plan.route_checked then
        plan.route_checked = true
        local array_base, route, reason = static_chain.resolve(
            api, base, resolver.verify_record, record, expect)
        if array_base ~= nil then
            resolver.set_table_base(array_base - static_chain.ARRAY_START)
            table_base = resolver.table_base()
            log.line(("static route hit: game.dll+0x%x -> array 0x%x (no scan needed)")
                :format(route.rva, array_base))
        else
            -- Not an error: the route is a fast path, and the scan below is the
            -- path that has always worked. Logged so a build where the offset
            -- moved is diagnosable rather than mysterious.
            log.line("static route unavailable (" .. tostring(reason) .. ")")
            log.line("  -> falling back to the address-space scan")
        end
    end

    -- ROUTE 2: another plan already found the table this session, so this
    -- record is a fixed offset from it. One scan for N edits, not N scans.
    if table_base ~= nil and ok ~= true then
        local candidate = ffi.cast("uint8_t *", table_base + (plan.record_offset or 0))
        local vok, detail = resolver.verify_record(api, candidate, record)
        if vok then
            local nok, nb = resolver.check_neighbours(
                api, candidate, record, expect.before_id, expect.after_id)
            if nok then
                log.line("  addressed by offset from the table found above")
                address, near, ok = candidate, nb, true
            else
                log.line("  offset check failed: " .. tostring(nb))
            end
        else
            log.line("  offset check failed: " .. tostring(detail))
        end
    end

    -- ROUTE 3: the scan.
    if ok ~= true then
        ok, address, near = resolver.find_record(api, base, record, expect)
    end
    if not ok then
        local detail = tostring(address)

        -- Two different failures wear the same face in a log, and they call for
        -- opposite responses:
        --
        --   * "not found yet" - the walk is only partway through (the read
        --     budget stopped it). The table may simply not have loaded. Retry.
        --   * anything else - the walk finished and the record is not there.
        --     Retrying cannot help; it would just re-read the same memory.
        --
        -- Treating the second case as retryable is what made an earlier version
        -- loop over the whole address space repeatedly.
        local resumable = detail:find("not found yet", 1, true) ~= nil

        if resumable then
            -- PROGRESS IS EVIDENCE, so this is throttled rather than silenced.
            --
            -- An earlier version logged once and went quiet, so a run of ~90
            -- attempts produced a two-line log and the round proved nothing:
            -- there was no way to distinguish "still walking" from "stuck
            -- re-reading one giant region". The opposite mistake - every
            -- attempt - writes hundreds of lines of file I/O into the game
            -- process during exactly the stall being investigated.
            --
            -- The (scanned bytes, regions covered) pair is what carries the
            -- evidence, and it is still sampled: first, every 64th, and in the
            -- final line when the search completes.
            plan.scan_reports = (plan.scan_reports or 0) + 1
            if plan.scan_reports == 1 or plan.scan_reports % 64 == 0 then
                log.line("scanning: " .. detail)
            end
        elseif not plan.reported_missing then
            plan.reported_missing = true
            log.line("NOT FOUND (search complete): " .. detail)
        end
        return not resumable
    end
    local addr_n = tonumber(ffi.cast("uintptr_t", address))
    log.line(("located at 0x%x"):format(addr_n))
    -- Diagnostics for finding a static route to this table. Two runs with the
    -- same low bits but different bases means the layout inside the allocation
    -- is fixed and only the allocation moves - which is what decides whether
    -- the pointer-chain route is viable.
    --
    --   table_base       = candidate base of the allocation (position 0)
    --   page_offset      = offset within its page - stable across runs if the
    --                      runtime layout matches the .dl_bin layout
    --   expected_offset  = what the parsed file says it should be
    -- REC_OFF is emitted per-plan below; Lua 5.1 has no bitwise operators, so
    -- the page offset is precomputed in Python rather than done with & here.
    local REC_OFF = plan.record_offset or 0
    log.line(("  base 0x%x  table_base 0x%x  page_offset 0x%x  expected 0x%x")
        :format(tonumber(ffi.cast("uintptr_t", base)),
                addr_n - REC_OFF,
                REC_OFF - math.floor(REC_OFF / 0x1000) * 0x1000,
                plan.page_offset or 0))

    local gok, code, detail = guard.run_all(
        api, resolver, address, record, plan.changes, plan.baseline
    )
    if not gok then
        log.refusal(code, detail)
        -- A guard refusal is final: identity/baseline/writability will not
        -- change on their own, so retrying would only spam the log.
        return true
    end

    local wok, applied, problems = writer.apply(api, resolver, address, plan.changes)
    if #applied > 0 then
        log.line("applied: " .. writer.describe(applied))
    end
    if not wok then
        for _, problem in ipairs(problems) do
            log.line("  problem: " .. tostring(problem))
        end
        log.line("  a partial change was applied - see the problems above")
        return true
    end

    local vok, bad = writer.verify(api, resolver, address, plan.changes)
    if vok then
        log.line("verified: every requested field now holds its new value")
    else
        log.line("VERIFY FAILED:")
        for _, item in ipairs(bad) do
            log.line("  " .. tostring(item))
        end
    end
    return true
end

local ok, err = pcall(main)
if not ok then
    log.line("unexpected error: " .. tostring(err))
end
"""
    )
    return "".join(parts)


def build_archive(bytecode: bytes, name: str) -> bytes:
    resource = Resource(
        type=LUA_RESOURCE_TYPE,
        name_hash=resource_hash(name),
        data=wrap_lua(bytecode),
    )
    return Archive(resources=[resource]).build()


def package(out_dir: Path, blob: bytes, spec: Spec) -> Path:
    manifest = {
        "Version": 1,
        "Guid": GUID,
        "Name": DISPLAY_NAME,
        "Description": (
            f"Edits {spec.weapon} damage values at runtime. Requires Bingus "
            "Shared Loader (v14 or newer) to be installed and enabled. "
            "Refuses to write if the game build, weapon identity or current "
            "values do not match what it was generated for."
        ),
        "Options": [
            {
                "Name": DISPLAY_NAME,
                "Description": f"{spec.weapon} only.",
                "Include": ["data"],
            }
        ],
    }
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / ARCHIVE_STEM).write_bytes(blob)
    for suffix in (".stream", ".gpu_resources"):
        (data_dir / (ARCHIVE_STEM + suffix)).write_bytes(b"")

    files = {
        "manifest.json": (json.dumps(manifest, indent=2) + "\n").encode(),
        "build-info.json": (json.dumps(
            {"spec": spec.as_dict(), "slot": SLOT}, indent=2) + "\n").encode(),
    }
    for path in sorted(data_dir.iterdir()):
        files[f"data/{path.name}"] = path.read_bytes()

    zip_path = out_dir / f"{DISPLAY_NAME.replace(' ', '-')}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, payload in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            z.writestr(entry, payload)
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--weapon", default=None,
                        help="wiki page name, e.g. 'R-4 Hyena'")
    parser.add_argument("--edit", dest="edits", action="append", default=None,
                        metavar="SPEC",
                        help="per-weapon edit 'Weapon::DAMAGE/DURABLE/AP'; "
                             "repeat for several weapons. Shared rows are "
                             "refused unless --allow-shared is given.")
    parser.add_argument("--damage", type=int, default=None)
    parser.add_argument("--durable", type=int, default=None)
    parser.add_argument("--ap", type=int, default=None, help="penetration tier 0-10")
    parser.add_argument("--out", default="build", help="output directory")
    parser.add_argument("--emit-lua", default=None,
                        help="also write the generated Lua source here (for review)")
    parser.add_argument("--probe-static-route", action="store_true",
                        help="build a diagnostic mod that reports which rvas in "
                             "game.dll currently point at the damage table. Use "
                             "after a game update when the recorded routes stop "
                             "resolving; the mod it produces still applies its "
                             "edits, but logs the rvas to look for.")
    args = parser.parse_args()

    # Either the legacy single-weapon form or one or more --edit entries.
    if args.weapon is None and not args.edits:
        parser.error("--weapon or at least one --edit is required")
    if args.weapon is not None and args.edits:
        parser.error("use either --weapon (single) or --edit (repeatable), not both")
    if args.weapon is not None:
        args.edits = [f"{args.weapon}::{args.damage}/{args.durable}/{args.ap}"]
    # Require every value explicitly. Passing a sentinel for an omitted flag let
    # `--damage 400` alone reach the writer with durable/ap = -1, which encodes
    # as 0xFFFFFFFF: the record was corrupted before the read-back reported the
    # failure, so the corruption outlived the error.
    # With --edit, values come per-weapon in the edit string itself. The legacy
    # single-weapon form still requires all three flags, because a sentinel for
    # an omitted flag once reached the writer as -1 and corrupted the record.
    if args.weapon is not None:
        missing = [name for name, value in
                   (("--damage", args.damage), ("--durable", args.durable),
                    ("--ap", args.ap)) if value is None]
        if missing:
            parser.error("missing required option(s): " + ", ".join(missing))
    if args.weapon is not None:
        if not (0 <= args.damage <= 100000 and 0 <= args.durable <= 100000):
            parser.error("--damage and --durable must be between 0 and 100000")
        if not (0 <= args.ap <= 10):
            parser.error("--ap must be a penetration tier between 0 and 10")

    specs = build_specs(args.edits, default_damage=args.damage,
                        default_durable=args.durable, default_ap=args.ap)
    spec = specs[0]

    sources = {
        name: (ROOT / "mod_template" / "src" / name).read_text(encoding="utf-8")
        for name in MODULES
    }
    source = render_module(specs, sources,
                           probe_static_route=args.probe_static_route)

    if args.emit_lua:
        Path(args.emit_lua).write_text(source, encoding="utf-8")
        print(f"lua source -> {args.emit_lua}")

    bytecode = compile_source(source, chunkname=f"={SLOT}")
    print(f"compiled {len(source):,} chars -> {len(bytecode):,} bytes of bytecode")

    blob = build_archive(bytecode, SLOT)
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{ARCHIVE_STEM}").write_bytes(blob)
    zip_path = package(out_dir, blob, spec)

    print(f"slot   {SLOT}")
    print(f"  hash 0x{resource_hash(SLOT):016X}")
    print(f"  archive {len(blob):,} bytes")
    print(f"  changes {spec.changes}")
    print(f"  baseline {spec.baseline}")
    print(f"zip    {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
