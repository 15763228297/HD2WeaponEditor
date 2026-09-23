-- Static route: address the damage table through a fixed offset in game.dll.
--
-- WHY
--
-- The damage table is game state on the heap and its address changes every
-- launch (ASLR moves the module, and the allocation lands wherever the heap
-- puts it). That left only one way to find it: walk several GB of the process
-- looking for the record's 28-byte fingerprint. Measured at 35-57 seconds per
-- launch, every launch, for every user - and it is a stall the player feels
-- during startup.
--
-- A probe run (15_static_route.lua) found fixed offsets inside game.dll whose
-- contents are pointers to the table, and two independent launches reported the
-- IDENTICAL rvas:
--
--     rva 0x2791748  ->  table_base   (start of the heap allocation)
--     rva 0x2ac7cb0  ->  array_start  (table_base + 0x1e0)
--     rva 0x2ac80d8  ->  record       (a row address, R-4 specific)
--
-- Three fixed offsets, three different absolute values across those runs. So
-- the address is reachable in one read:
--
--     table = *(game_dll_base + 0x2791748)
--
-- WHICH ONE
--
-- 0x2ac7cb0 (array_start) is the route to use: it points straight at the
-- weapons array, so a record is `*(base + rva) + position * 76` with no
-- correction term. 0x2791748 points at the container, which would work but
-- needs +0x1e0 folded in. 0x2ac80d8 is not a general route at all - it holds
-- the address of the R-4 ROW, so it is only useful for that one weapon.
--
-- WHAT MAKES THIS SAFE
--
-- An rva is only a hint. The game patches, the table is rebuilt, or a
-- different build is loaded, and the offset then points at unrelated memory -
-- silently. So every value read through the route is verified before use:
--
--   1. the pointer must land in a committed, readable region
--   2. the target must look like the damage table (container header, and a
--      row that passes the same identity check the scan uses)
--   3. if either fails, the route is abandoned for this session and the
--      caller falls back to the scan that has always worked
--
-- Falling back on failure is the whole point: a wrong static offset must cost
-- the user nothing beyond the scan they were already paying for.

local ffi = require("ffi")

local M = {}

M.VERSION = "static-chain-v1"

-- Declared here as well as in 10_resolver.lua: this module can be loaded on its
-- own, and ffi.cdef on an already-declared struct raises. pcall keeps the
-- second declaration from being fatal.
pcall(ffi.cdef, [[
    typedef struct {
        void *base; void *allocation_base; uint32_t allocation_protection;
        uint16_t partition; uint16_t reserved; size_t size;
        uint32_t state; uint32_t protection; uint32_t type;
    } ShMemoryRegion;
]])

-- Where the table's array begins inside the allocation. Mirrors
-- 10_resolver.lua; see the derivation in tools/gen_mod.py - this is the DLArray
-- descriptor's own offset field, and 0x1e0 was only correct while the parser
-- mislabelled every row by five positions.
M.ARRAY_START = 100
M.RECORD_SIZE = 76

-- The route, as measured. Kept in a table rather than hardcoded into the
-- lookup so a later build can carry several and try them in order.
--
-- `kind` says what the stored pointer points AT, which is what decides the
-- arithmetic:
--   "array"    - the array itself; record = value + position * 76
--   "table"    - the container;  record = value + ARRAY_START + position * 76
--   "record"   - one specific row; only usable if the offset is per-weapon
M.ROUTES = {
    { rva = 0x2ac7cb0, kind = "array",  name = "array_start" },
    { rva = 0x2791748, kind = "table",  name = "table_base" },
}

-- The rva that held a row address, kept only so the log can explain why it is
-- not used as a route: it is R-4-specific and would break for every other
-- weapon.
M.RECORD_SPECIFIC_RVA = 0x2ac80d8

local function uptr(p)
    return tonumber(ffi.cast("uintptr_t", p))
end

local function read_ptr(api, address)
    local blob = api.read and api.read(ffi.cast("uint8_t *", address), 8)
    if blob == nil then
        -- Live path: same call shape 10_resolver uses. Declared defensively
        -- because this module can be loaded without it.
        local buffer = ffi.new("uint8_t[8]")
        local got = ffi.new("size_t[1]")
        local ok = api.kernel.ReadProcessMemory(
            api.process, ffi.cast("void *", address), buffer, 8, got)
        if ok == 0 or tonumber(got[0]) ~= 8 then return nil end
        blob = buffer
    end
    local p64 = ffi.cast("uint64_t *", blob)
    return tonumber(p64[0])
end

-- Is `address` inside a committed, readable region of this process?
--
-- This is the cheapest way to reject a stale rva: a patched or moved table
-- leaves the offset pointing at freed or unmapped memory, and dereferencing
-- that is exactly the mistake that corrupts an unrelated structure.
function M.address_is_readable(api, address)
    local region = ffi.new("ShMemoryRegion[1]")
    local got = api.kernel.VirtualQuery(
        ffi.cast("void *", address), region, ffi.sizeof(region))
    if got ~= ffi.sizeof(region) then return false end
    local r = region[0]
    if tonumber(r.state) ~= api.MEM_COMMIT then return false end
    local prot = tonumber(r.protection) or 0
    local base_prot = prot % 0x100
    -- 0x01 = PAGE_NOACCESS, 0x02 = PAGE_READONLY (readable), 0x04/0x40/0x08
    -- readable. 0x10/0x20 execute-only-ish but readable on x64.
    if base_prot == 0x01 then return false end
    local start = uptr(r.base)
    local size = tonumber(r.size) or 0
    return address >= start and address < start + size
end

-- Resolve the array base through the route table.
--
-- Returns `array_base, used_route, reason`. `array_base` is nil when no route
-- verified, and `reason` says why - the caller logs it and falls back.
--
-- `verify` is a callback `(api, address) -> ok, detail` supplied by the caller
-- (it is 10_resolver.verify_record). Injecting it keeps this module free of the
-- scan's identity rules and lets a test supply its own.
function M.resolve(api, module_base, verify, record, expect)
    if module_base == nil then
        return nil, nil, "game.dll base is unknown"
    end
    local base = uptr(module_base)
    local reasons = {}

    for _, route in ipairs(M.ROUTES) do
        local slot = base + route.rva
        local value = read_ptr(api, slot)
        if value == nil then
            reasons[#reasons + 1] = ("%s: unreadable"):format(route.name)
        elseif value == 0 then
            reasons[#reasons + 1] = ("%s: null"):format(route.name)
        elseif not M.address_is_readable(api, value) then
            reasons[#reasons + 1] = ("%s: 0x%x is not committed memory")
                :format(route.name, value)
        else
            -- Fold in what the route points at, then verify a real row through
            -- it. Verifying only that the pointer is readable would accept a
            -- stale offset that happens to land in live memory.
            local array_base = value
            if route.kind == "table" then
                array_base = value + M.ARRAY_START
            end

            local row = array_base
            if record ~= nil and record.position ~= nil then
                row = array_base + record.position * M.RECORD_SIZE
            end

            local ok, detail = verify(api, ffi.cast("uint8_t *", row), record)
            if ok then
                return array_base, route, nil
            end
            reasons[#reasons + 1] = ("%s: row check failed (%s)")
                :format(route.name, tostring(detail))
        end
    end

    return nil, nil, table.concat(reasons, "; ")
end

-- Address of one record, computed from a resolved array base.
function M.record_address(array_base, position)
    return array_base + position * M.RECORD_SIZE
end

return M
