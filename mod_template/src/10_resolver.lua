--[[
10_resolver.lua -- locate the damage-record array in the running game.

WHY THIS IS NEEDED
------------------
The offline parse reads the decrypted `generated_damage_settings.dl_bin`, where a
damage record is a packed 76-byte struct. We verified the file layout equals the
runtime layout (tests/test_layout_matches_runtime.py), so a row index from the
file is a valid runtime row index.

What we do NOT have is the base address of that array in memory. The reference
mod ships its own `RESOLVER_RVA` / `ENTITY_OWNER_GLOBAL_RVA` constants, but they
are stored as LuaJIT internal constants inside its bytecode and could not be
recovered. So we locate the array ourselves, by content.

HOW
---
Search the writable regions of the main module for the 76-byte pattern of a
record we know exactly: the configured weapon's own original values, which the
GUI passes in as `baseline`. A single record match is not trusted - we then check
the neighbouring rows for self-consistency, because a 76-byte pattern can occur
by chance and writing to the wrong place is the failure we must not ship.

Everything is bounded: a byte cap on the scan, a cap on candidates, and a hard
requirement that the neighbour check passes. If nothing is found we report that
and stop. We never guess an address.
]]

local ffi = require("ffi")

local M = {
    VERSION = "resolver-v1",
    RECORD_SIZE = 76,
}

-- Field offsets inside one damage record (see tools/parse_dlbin.py, which the
-- Go sources confirm). Only the fields the editor can change are named.
M.OFF = {
    index = 0,
    damage = 4,
    durable = 8,
    ap = 12,        -- uint32[4]
    demolition = 28,
    force = 32,
    impulse = 36,
}

-- Scan limits.
--
-- The table is game state on the heap, so the search covers the whole committed
-- writable private address space rather than just the module. Each region is
-- read in chunks so a single huge mapping cannot allocate hundreds of MB, and a
-- candidate cap makes a weak pattern refuse instead of guessing.
-- Layout constants, so a located record can be turned into the base of its
-- array and back. Without these each additional record would need its own
-- address-space walk.
M.ARRAY_START = 0x1e0
M.RECORD_SIZE = 76

-- Base address of the damage array, once any record has been located.
-- nil until then. Consumers use it to address other rows by offset.
function M.table_base()
    return M.table_base_value
end

-- Set the table base from an outside source (the static route in
-- 16_static_chain.lua) rather than from a scan.
--
-- The value arriving here has already been verified against a real row, so this
-- is not a way to skip verification - it is how a resolved route publishes the
-- same fact a successful scan would. Keeping one storage location means every
-- consumer addresses rows the same way whatever found the table.
function M.set_table_base(value)
    M.table_base_value = value
end

-- Where the address-space walk starts.
--
-- It must start LOW, not at the module base. A Windows x64 process puts modules
-- high (game.dll was at 0x7ff936410000) and heap allocations far below that, in
-- roughly the 0x1_0000_0000 - 0x2_0000_0000 range. Walking upward from the
-- module base therefore searched only the top slice of the address space and
-- missed the heap entirely - which is what "pattern not found" actually meant.
M.SCAN_FLOOR = 0x10000

-- Upper bound for the walk. The x64 user space tops out just below this, so it
-- is a safety ceiling rather than a working limit.
M.MAX_ADDRESS = 0x7FFFFFFFFFFF

-- Hard budget on how much memory may be READ per scan attempt.
--
-- This is not a nicety. An earlier version walked the whole address space in
-- 4 MB chunks with no byte budget; on a loaded process that is gigabytes of
-- copies performed while the game is still streaming its startup assets, and it
-- crashed the game before the intro cinematic finished. The scan reads the game
-- process, so being conservative is the difference between "does not find it"
-- and "kills the game".
--
-- Bandwidth is NOT what makes this expensive; a single frame is. Every read
-- here is a synchronous ReadProcessMemory performed inside one frame callback,
-- so a 64 MB budget meant 64 copies back-to-back and a visible hitch on that
-- frame - the user reported the game being stuttery for the first ~40 seconds,
-- which matches the scan exactly.
--
-- 8 MB per attempt at ~8 attempts a second keeps the same ~64 MB/s while
-- cutting the worst-case stall per frame by 8x, which is what actually shows
-- up as frame time. The walk takes longer in wall-clock terms (a few minutes)
-- but it runs during loading, and it stops the moment the record is found.
--
-- The floor on this: an unbudgeted 4 MB-per-chunk walk running every frame is
-- what crashed the game outright.
--
-- Scanning resumes where the previous attempt stopped, so a small budget still
-- covers the whole address space - it just takes several attempts. That costs
-- nothing, because this runs while the game is loading anyway.
M.SCAN_BUDGET_BYTES = 8 * 1024 * 1024

-- How much of one region to read in a single ReadProcessMemory call. Small
-- enough that a single copy cannot spike memory pressure.
M.SCAN_CHUNK = 1024 * 1024

-- Stop after this many pattern hits. More than a couple means the pattern is too
-- weak for this table and we should refuse rather than keep guessing.
M.MAX_CANDIDATES = 64

local function bind()
    if M.api then return M.api end

    -- A test harness may pre-inject a fake API at `_G.__resolver_api`; using it
    -- lets this exact file be exercised offline against a simulated process
    -- image, which is the only safe way to develop address-scanning code.
    local injected = rawget(_G, "__resolver_api")
    if type(injected) == "table" then
        M.api = injected
        return M.api
    end

    -- The struct is re-declared on every load, and a host that already declared
    -- it (another mod, or a second load of this file) would make ffi.cdef raise.
    -- Declaring only what is missing keeps the module loadable in any order.
    local ok_cdef = pcall(ffi.cdef, [[
        int ReadProcessMemory(void *process, const void *address, void *buffer,
                              size_t size, size_t *read);
    ]])
    if not ok_cdef then
        -- Already declared: fine, the calls below still bind.
    end

    ffi.cdef [[
        void *GetModuleHandleA(const char *name);
        void *GetCurrentProcess(void);
        int ReadProcessMemory(void *process, const void *address, void *buffer,
                              size_t size, size_t *read);
        int WriteProcessMemory(void *process, void *address, const void *buffer,
                               size_t size, size_t *written);
        typedef struct {
            void *base; void *allocation_base; uint32_t allocation_protection;
            uint16_t partition; uint16_t reserved; size_t size;
            uint32_t state; uint32_t protection; uint32_t type;
        } ShMemoryRegion;
        size_t VirtualQuery(const void *address, void *region, size_t size);
    ]]
    local kernel = ffi.load("kernel32")
    M.api = {
        kernel = kernel,
        process = kernel.GetCurrentProcess(),
        MEM_COMMIT = 0x1000,
        PAGE_GUARD = 0x100,
        -- Readable-and-writable: the array is game state, not code. Excluding
        -- read-only pages both speeds the scan and avoids matching a copy of the
        -- table inside the executable image, which cannot be the live one.
        WRITABLE = 0x04,        -- PAGE_READWRITE
        WRITABLE2 = 0x40,       -- PAGE_EXECUTE_READWRITE
        WRITABLE3 = 0x08,       -- PAGE_WRITECOPY
    }
    return M.api
end

M.bind = bind

local function module_base(name)
    local api = bind()

    -- An injected API (offline harness) supplies its own module lookup. Asking
    -- `kernel` directly would bypass the harness and, in a test process, return
    -- the wrong thing or nil.
    if api.module_base then return api.module_base(name) end

    local handle = api.kernel.GetModuleHandleA(name)
    if handle == nil then return nil end
    return ffi.cast("uint8_t *", handle)
end

M.module_base = module_base

-- Read a range into a fresh buffer. Returns nil on any failure; a partial read
-- is treated as failure because a truncated buffer would misalign every
-- subsequent offset.
--
-- `api.read(address, size)` is the seam: the real API routes it to
-- ReadProcessMemory, an offline harness overrides the whole function with one
-- backed by a byte buffer. Scanning code cannot be developed against the live
-- game (a wrong address means corrupting state), so this indirection is what
-- makes the logic testable at all.
local function read_range(api, address, size)
    if api.read then return api.read(address, size) end

    local buffer = ffi.new("uint8_t[?]", size)
    local got = ffi.new("size_t[1]")
    local ok = api.kernel.ReadProcessMemory(
        api.process, address, buffer, size, got
    )
    if ok == 0 then return nil end
    if tonumber(got[0]) ~= size then return nil end
    return buffer
end

M.read_range = read_range

-- ---------------------------------------------------------------------------
-- Region enumeration: which parts of the address space are worth scanning.
-- ---------------------------------------------------------------------------

-- Yield {base, size} for each committed, writable region in [start, limit).
--
-- The scan must cover the whole process, NOT just the game.dll module. The
-- damage table is game state and lives on the heap; bounding the search to the
-- module's own address range found nothing and reported "pattern not found in
-- any writable region", which reads like the table moved rather than like the
-- search was looking in the wrong place entirely.
--
-- The module base is still passed in as the *starting point* - heap allocations
-- for a module's data tend to land after it, so beginning there keeps the common
-- case fast while the loop still walks on past the module's end.
function M.writable_regions(api, start, end_)
    local regions = {}
    local region = ffi.new("ShMemoryRegion[1]")
    local address = ffi.cast("uint8_t *", start)
    local limit = ffi.cast("uintptr_t", end_)
    local guard = 0
    local bit = require("bit")

    while ffi.cast("uintptr_t", address) < limit do
        guard = guard + 1
        -- 200k iterations is far beyond the region count of a normal process
        -- (thousands at most); this only stops a pathological walk.
        if guard > 200000 then break end

        local got = api.kernel.VirtualQuery(address, region, ffi.sizeof(region))
        if got ~= ffi.sizeof(region) then break end
        local r = region[0]
        local base = ffi.cast("uintptr_t", r.base)
        local size = tonumber(r.size)
        if size == nil or size <= 0 then break end
        local state = tonumber(r.state)
        local protection = tonumber(r.protection)

        -- PAGE_GUARD is a flag bit, not a base value; see 20_guard.lua.
        if state == api.MEM_COMMIT
           and bit.band(protection, api.PAGE_GUARD) == 0 then
            local prot = bit.band(protection, 0xFF)
            local writable = prot == api.WRITABLE
                          or prot == api.WRITABLE2
                          or prot == api.WRITABLE3
            -- Skip MEM_MAPPED/IMAGE regions: a copy of a table inside a mapped
            -- file is not the live one. 0x20000 is MEM_PRIVATE.
            local kind = tonumber(r.type)
            if writable and base >= ffi.cast("uintptr_t", start) and base < limit
               and (kind == nil or kind == 0x20000) then
                -- `r.base` comes straight out of the ShMemoryRegion struct and
                -- is typed `void *`. That matters downstream: LuaJIT will not add
                -- a number to a void pointer, so a consumer that does
                -- `region.base + offset` raises at runtime. Normalise to
                -- uint8_t* here so every region has a type that arithmetic works
                -- on, rather than leaving each caller to remember.
                regions[#regions + 1] = {
                    base = ffi.cast("uint8_t *", r.base), size = size,
                }
            end
        end

        local next_addr = base + size
        if next_addr <= ffi.cast("uintptr_t", address) then break end
        address = ffi.cast("uint8_t *", next_addr)
    end
    return regions
end

-- ---------------------------------------------------------------------------
-- Pattern building and matching.
-- ---------------------------------------------------------------------------

local function u32_at(blob, offset)
    local b = blob
    return tonumber(b[offset])
        + tonumber(b[offset + 1]) * 256
        + tonumber(b[offset + 2]) * 65536
        + tonumber(b[offset + 3]) * 16777216
end

M.u32_at = u32_at

-- The 28 bytes we can pin exactly for a record: type id, damage, durable and
-- the four AP values.
--
-- IMPORTANT: `record.type_id` is the raw value stored at offset 0, which is a
-- DamageInfoType ID and NOT the row's position in the array. Those are different
-- numbering schemes - positions run 0..633, ids run 4..639, and they only
-- coincide for part of the table (519 of 634 rows have id ~= position). Using
-- the position here was a real bug: it made 519 rows unfindable, and it looked
-- correct because the one row under test happened to be one where they match.
--
-- Both numbers are carried, and each is used for what it actually is:
--   record.type_id   - the value stored at +0; used for matching and identity
--   record.position  - the row index; used for scan offsets and GUI reference
function M.build_pattern(record)
    local pattern = ffi.new("uint8_t[28]")
    local function put(offset, value)
        pattern[offset]     = value % 256
        pattern[offset + 1] = math.floor(value / 256) % 256
        pattern[offset + 2] = math.floor(value / 65536) % 256
        pattern[offset + 3] = math.floor(value / 16777216) % 256
    end
    put(0, record.type_id)
    put(4, record.damage)
    put(8, record.durable)
    for i = 0, 3 do
        put(12 + i * 4, record.ap[i + 1])
    end
    return pattern
end

-- Compare `pattern` at every 4-byte-aligned offset of `blob` (a chunk read from
-- the target). Returns a list of offsets relative to the chunk.
function M.find_in(blob, size, pattern, pattern_len)
    local hits = {}
    local limit = size - pattern_len
    local last = pattern[pattern_len - 1]
    for offset = 0, limit, 4 do
        -- Cheap reject on the last byte before the full compare.
        if blob[offset + pattern_len - 1] == last then
            local match = true
            for i = 0, pattern_len - 1 do
                if blob[offset + i] ~= pattern[i] then match = false break end
            end
            if match then hits[#hits + 1] = offset end
        end
    end
    return hits
end

-- ---------------------------------------------------------------------------
-- The scan.
-- ---------------------------------------------------------------------------

-- Confirm `address` really is the record we want, by reading the fields that
-- are NOT part of the pattern and checking them too. This is what turns a
-- plausible byte match into evidence.
function M.verify_record(api, address, record)
    local buffer = read_range(api, address, M.RECORD_SIZE)
    if buffer == nil then return false, "unreadable" end

    local got = {
        index = u32_at(buffer, M.OFF.index),
        damage = u32_at(buffer, M.OFF.damage),
        durable = u32_at(buffer, M.OFF.durable),
        demolition = u32_at(buffer, M.OFF.demolition),
        force = u32_at(buffer, M.OFF.force),
        impulse = u32_at(buffer, M.OFF.impulse),
    }
    for i = 0, 3 do
        got["ap" .. i] = u32_at(buffer, M.OFF.ap + i * 4)
    end

    local problems = {}
    if got.index ~= record.type_id then
        problems[#problems + 1] = ("type id %d != %d"):format(got.index, record.type_id)
    end
    if got.damage ~= record.damage then
        problems[#problems + 1] = ("damage %d != %d"):format(got.damage, record.damage)
    end
    if got.durable ~= record.durable then
        problems[#problems + 1] = ("durable %d != %d"):format(got.durable, record.durable)
    end
    for i = 0, 3 do
        if got["ap" .. i] ~= record.ap[i + 1] then
            problems[#problems + 1] = ("ap[%d] %d != %d"):format(i, got["ap" .. i], record.ap[i + 1])
        end
    end
    -- Forces are checked when the caller supplies them. They are the fields a
    -- different mod is most likely to have changed, so a mismatch here is
    -- information, not necessarily a rejection - the caller decides.
    if record.demolition ~= nil and got.demolition ~= record.demolition then
        problems[#problems + 1] = ("demolition %d != %d"):format(got.demolition, record.demolition)
    end

    if #problems > 0 then return false, table.concat(problems, "; ") end
    return true, got
end

-- Read the record at `address + delta*RECORD_SIZE` and return its damage and
-- durable, or nil. Used for the neighbour consistency check.
--
-- The address arithmetic is done in Lua numbers and cast once. Building it by
-- mixing `uintptr_t` and `intptr_t` cdata in one expression depends on LuaJIT's
-- integer-conversion rules for the negative case, which is not worth relying on
-- in code whose whole job is to be right about addresses.
local function neighbour_values(api, address, delta)
    local target = ffi.cast("uintptr_t", address) + delta * M.RECORD_SIZE
    local buffer = read_range(api, ffi.cast("uint8_t *", target), 12)
    if buffer == nil then return nil end
    return u32_at(buffer, M.OFF.index), u32_at(buffer, M.OFF.damage), u32_at(buffer, M.OFF.durable)
end

M.neighbour_values = neighbour_values

-- A hit is only accepted when the rows around it look like a real table.
--
-- The `+0` field is a DamageInfoType ID, NOT the row number. The array stores
-- records in its own order and the IDs inside are non-monotonic (`4, 5, 18, 19,
-- 337, 20, 21, ...` in the shipped file). An earlier version of this check
-- asserted `id == row +/- 1` and passed only by luck on the low rows where the
-- two happen to coincide, which is exactly how a wrong assumption survives a
-- test run.
--
-- The correct check is against the real ID order, which the offline parse knows:
-- the caller passes the IDs of the rows before and after the target, and we
-- require an exact match. That is far stronger than a range check - a
-- coincidental 76-byte match would have to reproduce both neighbours too.
function M.check_neighbours(api, address, record, expect_before_id, expect_after_id)
    local before_i, before_d, before_u = neighbour_values(api, address, -1)
    local after_i, after_d, after_u = neighbour_values(api, address, 1)

    if before_i == nil or after_i == nil then
        return false, "neighbours unreadable"
    end
    if expect_before_id ~= nil and before_i ~= expect_before_id then
        return false, ("previous row id %d, expected %d"):format(before_i, expect_before_id)
    end
    if expect_after_id ~= nil and after_i ~= expect_after_id then
        return false, ("next row id %d, expected %d"):format(after_i, expect_after_id)
    end
    if before_d == 0 and before_u == 0 and after_d == 0 and after_u == 0 then
        return false, "neighbours are empty - probably not the table"
    end
    return true, {
        before = { index = before_i, damage = before_d, durable = before_u },
        after = { index = after_i, damage = after_d, durable = after_u },
    }
end

-- Summarise the regions a scan would cover. Called when a record is not found,
-- so the log distinguishes the two failure classes that look identical from the
-- outside:
--
--   * a window problem - the heap was never walked (few regions, no low
--     addresses, tiny byte count);
--   * a layout problem - the heap was walked and the pattern still did not
--     appear (many regions, plausible byte count, no hits).
--
-- Without this the only symptom is "pattern not found", which reads like the
-- table moved and sent two debugging rounds in the wrong direction.
function M.describe_regions(api, regions)
    local total = 0
    local lowest, highest = nil, nil
    for _, region in ipairs(regions) do
        local base = tonumber(ffi.cast("uintptr_t", region.base))
        total = total + region.size
        if lowest == nil or base < lowest then lowest = base end
        local top = base + region.size
        if highest == nil or top > highest then highest = top end
    end
    local parts = {
        ("%d region(s)"):format(#regions),
        ("%d bytes"):format(total),
    }
    if lowest ~= nil then
        parts[#parts + 1] = ("range 0x%x..0x%x"):format(lowest, highest)
    end
    -- A handful of bases makes it obvious whether the low heap is represented.
    local sample = {}
    for i = 1, math.min(#regions, 6) do
        sample[#sample + 1] = ("0x%x"):format(
            tonumber(ffi.cast("uintptr_t", regions[i].base)))
    end
    if #sample > 0 then
        parts[#parts + 1] = "first bases: " .. table.concat(sample, ", ")
    end
    return table.concat(parts, "; ")
end

-- Scan the module for `record`. Returns
--   ok, address, neighbours | nil, reason
--
-- `expect` carries what the offline parse knows about this row and its
-- neighbours, which is what makes a positive identification possible:
--   expect.before_id / expect.after_id  - the DamageInfoType IDs of the rows
--       immediately before and after this one in the array (NOT row +/- 1; the
--       array's order is its own).
--   expect.next  - optional {damage, durable} of the following row.
function M.find_record(api, base, record, expect)
    expect = expect or {}
    local pattern = M.build_pattern(record)
    local pattern_len = 28

    -- FAST PATH: if a previous run located this record, check that address
    -- first. One read instead of a full-space walk.
    --
    -- Two independent runs found the R-4 record at different absolute
    -- addresses (ASLR/heap placement moves) but with the SAME offset inside
    -- the allocation, which is why the base alone cannot be remembered - only
    -- the whole address can, and it must be verified before use.
    local known = M.known_addresses and M.known_addresses[record.type_id]
    if known ~= nil then
        local addr = ffi.cast("uint8_t *", known)
        local ok, detail = M.verify_record(api, addr, record)
        if ok then
            local near_ok, near = M.check_neighbours(
                api, addr, record, expect.before_id, expect.after_id)
            if near_ok then
                return true, addr, near, 0
            end
        end
        -- Stale (the process moved it); fall through and search again.
        M.known_addresses[record.type_id] = nil
    end

    -- Same seam as read_range: an offline harness supplies its own region list,
    -- so the scan can be exercised without VirtualQuery.
    --
    -- The window is the whole user address space, walked from SCAN_FLOOR. It
    -- deliberately does NOT start at the module base: modules are mapped high
    -- and the heap sits far below them, so a window opened at the base searches
    -- the wrong end of memory and reports "not found" while the data is present
    -- the entire time.
    -- Enumerate ONCE and cache, then filter per call.
    --
    -- Enumeration is the expensive part, not reading. A live game has ~13000
    -- regions, so every `find_record` call was walking the entire address space
    -- with thousands of VirtualQuery calls before reading a single byte - at an
    -- attempt every 8 frames, that is continuous enumeration and it is what
    -- made the game stutter even after the read budget was cut to 8 MB.
    --
    -- The list is re-enumerated only when the cursor has passed its end (the
    -- walk is finished or the layout changed materially), never per attempt.
    local now_a = nil
    local cached = M.cached_regions
    if cached == nil or cached.floor ~= M.SCAN_FLOOR
       or cached.ceiling ~= M.MAX_ADDRESS then
        local fresh = api.writable_regions
            and api.writable_regions(api, M.SCAN_FLOOR, M.MAX_ADDRESS)
            or M.writable_regions(api, M.SCAN_FLOOR, M.MAX_ADDRESS)
        cached = { regions = fresh, floor = M.SCAN_FLOOR,
                   ceiling = M.MAX_ADDRESS }
        M.cached_regions = cached
    end

    -- Regions entirely below the cursor are already searched; drop them from
    -- this pass so later attempts do not re-walk the same early memory.
    local all = cached.regions
    local cursor0 = (M.scan_state and M.scan_state.cursor) or M.SCAN_FLOOR
    local regions = {}
    for i = 1, #all do
        local rb = tonumber(ffi.cast("uintptr_t", all[i].base))
        if rb + all[i].size > cursor0 then
            regions[#regions + 1] = all[i]
        end
    end

    -- Resume state: the walk continues where the previous call stopped.
    --
    -- The cursor is an ABSOLUTE ADDRESS, not a region index. That distinction
    -- is the whole fix. Two earlier attempts keyed resume on things that vary
    -- in a live process and never vary in a test:
    --
    --   * `state.regions ~= regions` - the list is rebuilt every call, so this
    --     was always false and every call restarted.
    --   * `#state.regions ~= #regions` - a live game ALLOCATES while we scan;
    --     the count moved 13320 -> 13394 across attempts in the field, so this
    --     restarted too. Measured: every attempt re-read the same opening
    --     regions and the search never got past region 3.
    --
    -- An address survives both: whatever the list does, "resume at 0x1a2b3c"
    -- means the same thing. Regions entirely below the cursor are skipped on
    -- the next pass.
    local state = M.scan_state
    if state == nil or state.floor ~= M.SCAN_FLOOR
       or state.ceiling ~= M.MAX_ADDRESS or state.cursor == nil then
        state = {
            cursor = M.SCAN_FLOOR, scanned = 0, rejections = {},
            floor = M.SCAN_FLOOR, ceiling = M.MAX_ADDRESS,
        }
        M.scan_state = state
    end

    local budget = M.SCAN_BUDGET_BYTES

    local candidates = 0
    local cursor = state.cursor

    -- Walk the CURRENT region list, skipping everything below the cursor.
    -- The list is re-enumerated each call (a live game allocates while we
    -- scan), so progress cannot be expressed as an index into it - only an
    -- address survives. `covered` counts how much of this pass we got through.
    local covered = 0

    for i = 1, #regions do
        local region = regions[i]
        local region_base = tonumber(ffi.cast("uintptr_t", region.base))
        local region_end = region_base + region.size
        if region_end <= cursor then
            covered = i
        elseif region.size >= pattern_len then
            -- First unvisited byte of this region (clamped to its start).
            local at = region_base
            if cursor > at then at = cursor end

            while at < region_end do
                local remaining = region_end - at
                local chunk = math.min(M.SCAN_CHUNK, remaining, math.max(budget, 0))
                if chunk < pattern_len then break end

                local blob = read_range(api, ffi.cast("uint8_t *", at), chunk)
                if blob == nil then
                    -- Unreadable: move to the next region rather than stall.
                    break
                end

                state.scanned = state.scanned + chunk
                budget = budget - chunk
                cursor = at + chunk
                state.cursor = cursor

                for _, offset in ipairs(M.find_in(blob, chunk, pattern, pattern_len)) do
                    candidates = candidates + 1
                    if candidates > M.MAX_CANDIDATES then
                        M.scan_state = nil
                        return nil, ("more than %d pattern hits - refusing to guess")
                            :format(M.MAX_CANDIDATES)
                    end
                    local address = ffi.cast("uint8_t *", at + offset)
                    local ok, detail = M.verify_record(api, address, record)
                    if ok then
                        local near_ok, near = M.check_neighbours(
                            api, address, record,
                            expect.before_id, expect.after_id
                        )
                        if near_ok then
                            local conflict = nil
                            if expect.next ~= nil then
                                if near.after.damage ~= expect.next.damage
                                   or near.after.durable ~= expect.next.durable then
                                    conflict = ("next row is %d/%d, expected %d/%d")
                                        :format(near.after.damage, near.after.durable,
                                                expect.next.damage, expect.next.durable)
                                end
                            end
                            if conflict == nil then
                                M.scan_state = nil      -- found it
                                M.known_addresses = M.known_addresses or {}
                                M.known_addresses[record.type_id] =
                                    tonumber(ffi.cast("uintptr_t", address))
                                -- Remember the BASE as well, so other records
                                -- can be addressed by offset instead of being
                                -- searched for: one walk for N edits, not N.
                                if M.RECORD_SIZE ~= nil and record.position ~= nil then
                                    M.table_base_value = tonumber(
                                        ffi.cast("uintptr_t", address))
                                        - (M.ARRAY_START + record.position * M.RECORD_SIZE)
                                end
                                return true, address, near, state.scanned
                            end
                            state.rejections[#state.rejections + 1] = conflict
                        else
                            state.rejections[#state.rejections + 1] = near
                        end
                    else
                        state.rejections[#state.rejections + 1] = detail
                    end
                end

                -- Advance, keeping pattern_len-1 bytes of overlap so a match
                -- spanning a chunk boundary is still seen.
                local step = chunk - (pattern_len - 1)
                if step <= 0 then break end
                at = at + step

                if budget <= 0 then
                    -- Budget spent: stop and resume at the cursor next call.
                    return nil, ("pattern not found yet: scanned %d bytes total, "
                        .. "now at 0x%x, %d of %d region(s)")
                        :format(state.scanned, cursor, covered, #regions)
                end
            end
            covered = i
        else
            covered = i
        end
    end

    -- Walked everything without a match.
    local scanned_total = state.scanned
    local rejections = state.rejections
    M.scan_state = nil
    if #rejections > 0 then
        return nil, ("%d candidate(s) rejected: %s"):format(#rejections, rejections[1])
    end
    return nil, ("pattern not found: scanned %d bytes across %d region(s); %s")
        :format(scanned_total, #regions, M.describe_regions(api, regions))
end
return M
