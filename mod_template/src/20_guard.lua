--[[
20_guard.lua -- the three checks that must pass before anything is written.

WHY THIS EXISTS
---------------
The failure this prevents is not "the mod does nothing" - it is "the mod changes
something we did not intend". Two ways that happens:

  * the address is wrong (a coincidental pattern match, or a game update moved
    the table): writing there corrupts unrelated game state;
  * the address is right but another mod already changed the field: writing our
    expected-old-value over it silently produces a value neither mod intended.

The reference mod in this install refuses to write unless three checks pass, and
its failure strings were recovered from its bytecode:

    <weapon> WeaponData identity mismatch
    <weapon> WeaponData template is not the expected protected data: %s
    baseline mismatch at field %d: expected %d, found %s (mod conflict or unsupported build)
    unsupported game.dll; no change applied

That structure is copied here deliberately: identity, then writability, then a
per-field baseline comparison. On any failure the answer is to stop and log, not
to try harder. A missing change is recoverable; a wrong write is not.

Order matters. Identity first (is this even the right record?), writability
second (would the write fault?), baseline last (has someone else touched it?).
]]

local M = { VERSION = "guard-v1" }

-- Guard outcomes. Codes are used in tests; text is for the log.
M.OK = "ok"
M.ERR_IDENTITY = "identity mismatch"
M.ERR_READ = "record read failed"
M.ERR_NOT_WRITABLE = "record is not writable"
M.ERR_BASELINE = "baseline mismatch"
M.ERR_LAYOUT = "record does not match the expected layout"

-- ---------------------------------------------------------------------------
-- 1. Identity
-- ---------------------------------------------------------------------------

-- Confirm the address holds the record we mean to edit.
--
-- Identity is the record's **type id** - the value at offset 0. That is what
-- distinguishes one record from another; two different weapons routinely share
-- the same damage numbers, so the values cannot serve as identity.
--
-- Comparing the values here instead (an earlier version did) made "identity
-- mismatch" fire for what is really a mod conflict, and left `baseline` with
-- nothing to check that identity had not already rejected. The split is:
--   identity  - is this the right record?
--   baseline  - do its values still say what we expect?
function M.check_identity(api, resolver, address, record, _baseline)
    local buffer = resolver.read_range(api, address, 4)
    if buffer == nil then
        return false, M.ERR_READ, "could not read the record"
    end

    local type_id = resolver.u32_at(buffer, resolver.OFF.index)
    if type_id ~= record.type_id then
        return false, M.ERR_IDENTITY,
            ("type id is %d, expected %d"):format(type_id, record.type_id)
    end

    return true, M.OK, { type_id = type_id }
end

-- ---------------------------------------------------------------------------
-- 2. Writability
-- ---------------------------------------------------------------------------

-- Check the page holding `address` is actually writable before touching it.
--
-- ReadProcessMemory succeeding does NOT imply writability: a read-only page
-- reads fine and faults on write, and a fault here is a crash, not a caught
-- error - `pcall` does not intercept an access violation.
--
-- This asks the OS about the page containing `address`. It deliberately does
-- NOT reuse the resolver's region enumerator: that one takes a [start, limit)
-- window and returns regions whose *base* lies inside it, which is the right
-- filter for "what is worth scanning in this module" and the wrong question for
-- "is this one address writable" - a region's base is normally well below the
-- address being checked.
function M.check_writable(api, address)
    -- An injected API (offline harness) answers directly.
    if api.is_writable then
        local ok, why = api.is_writable(address)
        if ok then return true, M.OK end
        return false, M.ERR_NOT_WRITABLE, why or "harness refused"
    end

    local region, why = M.describe_region(api, address)
    if region == nil then
        return false, M.ERR_NOT_WRITABLE, why or "unknown page state"
    end

    local state = tonumber(region.state)
    local prot = tonumber(region.protection)
    if state ~= 0x1000 then                       -- MEM_COMMIT
        return false, M.ERR_NOT_WRITABLE,
            ("page is not committed (state=0x%x)"):format(state)
    end
    -- PAGE_GUARD is a bit flag, not a base protection value: it sits alongside
    -- the base type (0x104 = READWRITE|GUARD). Testing `prot % 0x100 == 0x100`
    -- can never be true because a value mod 256 is always < 256, so guard pages
    -- used to pass this check. Touching one raises STATUS_GUARD_PAGE_VIOLATION -
    -- an access violation, which pcall does not catch.
    local bit = require("bit")
    if bit.band(prot, 0x100) ~= 0 then
        return false, M.ERR_NOT_WRITABLE, "page is a guard page"
    end
    local base_prot = bit.band(prot, 0xFF)
    if base_prot ~= 0x04 and base_prot ~= 0x08 and base_prot ~= 0x40 then
        return false, M.ERR_NOT_WRITABLE,
            ("page is not writable (%s)"):format(why)
    end
    return true, M.OK, why
end

-- Ask the OS what a page is, for the log. Best effort: never fails loudly.
function M.describe_region(api, address)
    if api.kernel == nil or api.kernel.VirtualQuery == nil then
        return nil, "no VirtualQuery available"
    end
    local ffi = require("ffi")
    local region = ffi.new("ShMemoryRegion[1]")
    local got = api.kernel.VirtualQuery(
        ffi.cast("void *", address), region, ffi.sizeof(region)
    )
    if got ~= ffi.sizeof(region) then
        return nil, "VirtualQuery failed"
    end
    local r = region[0]
    local prot = tonumber(r.protection)
    local state = tonumber(r.state)
    local names = {
        [0x01] = "noaccess", [0x02] = "readonly", [0x04] = "readwrite",
        [0x08] = "writecopy", [0x10] = "execute", [0x20] = "execute_read",
        [0x40] = "execute_readwrite", [0x80] = "execute_writecopy",
    }
    return r, ("state=0x%x protection=0x%x(%s)")
        :format(state, prot, names[prot % 0x100] or "?")
end

-- ---------------------------------------------------------------------------
-- 3. Baseline
-- ---------------------------------------------------------------------------

-- Compare every field we are about to change against the value it is supposed to
-- have right now. A mismatch means another mod (or a game update) already moved
-- it, and overwriting would discard their change without telling anyone.
--
-- Returns per-field detail so the log says exactly which field disagreed.
--
-- Field names come from the same vocabulary the writer uses (`damage`,
-- `durable`, `ap0`..`ap3`); `resolver.OFF` stores the AP base as `ap`, so the
-- four individual names are derived here rather than looked up directly.
function M.field_offset(resolver, field)
    local off = resolver.OFF[field]
    if off ~= nil then return off end
    local i = field:match("^ap([0-3])$")
    if i then return resolver.OFF.ap + tonumber(i) * 4 end
    return nil
end

function M.check_baseline(api, resolver, address, changes, baseline)
    local buffer = resolver.read_range(api, address, resolver.RECORD_SIZE)
    if buffer == nil then
        return false, M.ERR_READ, { field = "record", detail = "unreadable" }
    end

    local mismatches = {}
    for field, target in pairs(changes) do
        local offset = M.field_offset(resolver, field)
        if offset == nil then
            mismatches[#mismatches + 1] = {
                field = field, detail = "unknown field - refusing to guess its offset",
            }
        else
            local found = resolver.u32_at(buffer, offset)
            local expected = baseline[field]
            if expected == nil then
                mismatches[#mismatches + 1] = {
                    field = field, detail = "no baseline recorded for this field",
                }
            elseif found ~= expected then
                mismatches[#mismatches + 1] = {
                    field = field,
                    detail = ("expected %d, found %d%s"):format(
                        expected, found,
                        found == target and " (already at the target value)" or ""
                    ),
                }
            end
        end
    end

    if #mismatches > 0 then
        return false, M.ERR_BASELINE, mismatches
    end
    return true, M.OK, nil
end

-- ---------------------------------------------------------------------------
-- All of it, in order.
-- ---------------------------------------------------------------------------

-- `changes` maps a field name to its new value; `baseline` holds the value each
-- field is expected to have right now. Both are plain tables.
--
-- Returns ok, code, detail. `detail` is a table on baseline failure (one entry
-- per disagreeing field) and a string otherwise.
function M.run_all(api, resolver, address, record, changes, baseline)
    local ok, code, detail = M.check_identity(api, resolver, address, record, baseline)
    if not ok then return false, code, detail end

    ok, code, detail = M.check_writable(api, address)
    if not ok then return false, code, detail end

    ok, code, detail = M.check_baseline(api, resolver, address, changes, baseline)
    if not ok then return false, code, detail end

    return true, M.OK, nil
end

return M
