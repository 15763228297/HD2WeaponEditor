-- Static-route probe: find a fixed pointer to the damage table inside game.dll.
--
-- WHY THIS EXISTS
--
-- The damage table is game state that lives on the heap, and its address is
-- different on every launch (ASLR moves the module base, and the allocation
-- lands wherever the heap puts it). Because the address cannot be predicted,
-- the editor has to search the whole address space for it on every single
-- launch - measured at 35-51 seconds, every time, for every user.
--
-- A pointer chain would replace that with a handful of reads: if some fixed
-- offset inside game.dll holds a pointer to the table, then
--
--     table = *(game.dll_base + RVA)
--
-- costs one read and takes microseconds. The community reversing work confirms
-- this pattern exists for weapon data (`game.dll + 1CA7090` is described there
-- as an array of pointers to weapon damage structs).
--
-- This module answers the only question that decides whether such a route is
-- available for THIS build:
--
--     does the module's own data hold a pointer to the table, and if so at
--     what RVA?
--
-- It is deliberately a REPORT, not a fix. It scans and logs the RVA of every
-- hit. The same RVA across two launches means a static route exists and is
-- worth implementing; different RVAs, or no hit, means the address is reached
-- some other way and the search has to stay.
--
-- Only the module's own writable sections are scanned. Those are a few MB,
-- whereas the process is several GB - small enough to scan inside a frame
-- callback without the stutter the full walk causes.

local ffi = require("ffi")
local bit = require("bit")

local M = {}

M.VERSION = "static-route-v1"

-- How much module data one `step` may read. The module's writable data is only
-- a few MB, so this finishes in a handful of frames and never puts a large
-- copy inside a single frame - the same reasoning as the scan budget in
-- 10_resolver.lua.
--
-- Writable as a module field, not a file-local constant: the shipped value is
-- the default, and a test can lower it to force the chunking path (which is
-- where a pointer can be lost across a boundary). A value that cannot be
-- varied in a test is a value whose boundary handling is never exercised.
M.BUDGET = 1024 * 1024
M.CHUNK = 1024 * 1024

-- Pointers are 8-byte aligned inside the structures this game builds, so only
-- 8-byte-aligned positions are examined. That halves the candidate count and,
-- because each chunk is a multiple of 8 bytes starting at a page-aligned base,
-- a pointer can never straddle two chunks unnoticed.
M.ALIGN = 8

-- A weak match (a value that merely looks like an address) is useless, but a
-- cap keeps a pathological module from filling memory with hits.
M.MAX_HITS = 32

-- Where the array begins inside the table allocation. This is the DLArray
-- descriptor's own offset field, not a tunable - see the derivation in
-- tools/gen_mod.py. It was 0x1e0 while the parser mislabelled rows by five
-- positions; that compensation is gone now that positions are true.
M.ARRAY_START = 100
M.HEADER_END = 0x18

-- Bytes per damage record. Declared here rather than borrowed from the
-- resolver: the blind walk below measures a candidate table against it, and
-- this module is loaded on its own in tests.
M.RECORD_SIZE = 76

local MEM_IMAGE = 0x1000000

-- The struct is declared by 10_resolver.lua, but this module can be loaded on
-- its own (tests, or a future build that drops the scan), so declare it
-- defensively rather than assuming the other module ran first.
local ok_cdef = pcall(ffi.cdef, [[
    typedef struct {
        void *base; void *allocation_base; uint32_t allocation_protection;
        uint16_t partition; uint16_t reserved; size_t size;
        uint32_t state; uint32_t protection; uint32_t type;
    } ShMemoryRegion;
]])
if not ok_cdef then
    -- Already declared; the allocation below still works.
end

local function uptr(p)
    return tonumber(ffi.cast("uintptr_t", p))
end

-- Read `size` bytes from `address` into a fresh buffer.
--
-- This module does NOT reuse `api.read`. That seam exists on 10_resolver.lua's
-- api table ONLY when a test harness injects it - the real api table has no
-- `read` field, it has `kernel.ReadProcessMemory`. A probe that called
-- `api.read` therefore got nil for every region in the live game, treated each
-- as unreadable, skipped all of them, and reported "NOT FOUND: 0 byte(s)
-- scanned" - a confident negative with no scan behind it.
--
-- A nil-tolerant `api.read and api.read(...)` is the shape of that bug: the
-- fallback never runs, because the failure is indistinguishable from a
-- skipped page. The offline seam is checked explicitly instead.
local function read_range(api, address, size)
    if api.read then return api.read(address, size) end

    local buffer = ffi.new("uint8_t[?]", size)
    local got = ffi.new("size_t[1]")
    local ok = api.kernel.ReadProcessMemory(
        api.process, address, buffer, size, got
    )
    if ok == 0 then return nil end
    -- A short read is a failure: the buffer tail would be zeros, and a zeroed
    -- tail could either hide a real pointer or manufacture a false one.
    if tonumber(got[0]) ~= size then return nil end
    return buffer
end

-- Writable, committed, guard-free regions that belong to the module itself.
--
-- The walk is bounded by the image rather than by a byte count: it stops as
-- soon as it reaches a region that is not MEM_IMAGE, which is past the last of
-- the module's own sections. Trusting an owner pointer comparison instead would
-- break on modules whose sections are separate allocations.
function M.module_regions(api, module_base)
    local regions = {}
    local writable_bytes = 0
    local readonly_bytes = 0
    local region = ffi.new("ShMemoryRegion[1]")
    local base_n = uptr(module_base)
    local address = base_n
    local guard = 0

    while true do
        guard = guard + 1
        -- A PE has a handful of sections; this only stops a pathological walk.
        if guard > 512 then break end

        local got = api.kernel.VirtualQuery(
            ffi.cast("uint8_t *", address), region, ffi.sizeof(region))
        if got ~= ffi.sizeof(region) then break end

        local r = region[0]
        local rbase = uptr(r.base)
        local size = tonumber(r.size)
        if size == nil or size <= 0 then break end

        -- Past the image: everything further is another mapping entirely.
        if tonumber(r.type) ~= MEM_IMAGE then break end

        local state = tonumber(r.state)
        local protection = tonumber(r.protection)
        if state == api.MEM_COMMIT and bit.band(protection, api.PAGE_GUARD) == 0 then
            local prot = bit.band(protection, 0xFF)
            local writable = prot == api.WRITABLE
                          or prot == api.WRITABLE2
                          or prot == api.WRITABLE3
            -- Scan read-only DATA and all writable data; skip executable code.
            --
            -- The first draft scanned every committed section. That is wrong in
            -- both directions: it missed nothing, but game.dll's .text is
            -- hundreds of MB and cannot hold what this probe looks for - a
            -- variable holding a heap address. Code reaches data through
            -- RIP-relative addressing, which never materialises the 8-byte
            -- value; the only absolute pointers in a code section are import
            -- thunks to functions. Skipping the execute pages (0x10 / 0x20 /
            -- 0x80) turns a probe that would read the whole image over
            -- thousands of frames into one that reads a few tens of MB.
            --
            -- PAGE_READONLY (0x02) is kept: a global the game never writes to
            -- is `const` and lands in read-only data, which is exactly the
            -- shape a built-in table pointer has. Skipping it would produce a
            -- confident "no static route" that meant nothing.
            local code = prot == 0x10 or prot == 0x20 or prot == 0x80
            if not code and prot ~= 0x01 then    -- 0x01 = PAGE_NOACCESS
                regions[#regions + 1] = {
                    base = ffi.cast("uint8_t *", r.base), size = size,
                    writable = writable,
                }
                if writable then
                    writable_bytes = writable_bytes + size
                else
                    readonly_bytes = readonly_bytes + size
                end
            end
        end

        local next_addr = rbase + size
        if next_addr <= address then break end
        address = next_addr
    end

    return regions, readonly_bytes, writable_bytes
end

local function total_size(regions)
    local n = 0
    for _, r in ipairs(regions) do n = n + r.size end
    return n
end

-- Begin a probe run.
--
-- `table_base` is the address the editor computed for the start of the table
-- allocation (record address minus the array start and the row's own offset).
-- The record address itself is included as a target too: a module global could
-- hold either, and which one it holds is itself worth knowing.
function M.begin(api, module_base, table_base, opts)
    opts = opts or {}
    local array_start = opts.array_start or M.ARRAY_START
    local record_address = opts.record_address

    local targets = {}
    local function add(name, value)
        if value ~= nil then
            targets[#targets + 1] = { name = name, value = value }
        end
    end
    add("table_base", table_base)
    add("header_end", table_base + M.HEADER_END)
    add("array_start", table_base + array_start)
    add("record", record_address)
    -- A module global usually points at the START of the heap allocation, and
    -- the damage container may not be the whole allocation: an owner object
    -- with a vtable or a bookkeeping header would put the container a few
    -- words in, so the stored pointer lands just BELOW table_base. Those two
    -- offsets are the common shapes; each extra target costs nothing at scan
    -- time because the per-position cost is one lookup regardless of how many
    -- names share a bucket.
    add("owner-0x10", table_base - 0x10)
    add("owner-0x8", table_base - 0x8)

    local regions, readonly_bytes, writable_bytes
    if api.module_regions then
        regions, readonly_bytes, writable_bytes = api.module_regions(api, module_base)
    else
        regions, readonly_bytes, writable_bytes = M.module_regions(api, module_base)
    end

    -- Indexed by the low 32 bits so the inner loop does one array read and one
    -- table lookup per candidate position. Comparing 64-bit values directly
    -- would mean two reads plus arithmetic at every offset of every chunk.
    local wanted = {}
    for _, t in ipairs(targets) do
        local low = t.value % 4294967296
        local high = math.floor(t.value / 4294967296)
        local bucket = wanted[low]
        if bucket == nil then
            bucket = {}
            wanted[low] = bucket
        end
        bucket[#bucket + 1] = { name = t.name, high = high }
    end

    return {
        module_base = module_base,
        table_base = table_base,
        targets = targets,
        wanted = wanted,
        regions = regions,
        readonly_bytes = readonly_bytes or 0,
        writable_bytes = writable_bytes or 0,
        ri = 1,
        at = nil,
        hits = {},
        scanned = 0,
        total = total_size(regions),
    }
end

-- Read one budget's worth of module data, then return so the caller can hand
-- the frame back to the game.
--
-- Returns `done, message`. `message` is a progress or completion line for the
-- log, or nil when there is nothing to say.
function M.step(api, state)
    local budget = M.BUDGET
    local chunk_limit = M.CHUNK

    while state.ri <= #state.regions do
        local region = state.regions[state.ri]
        local rbase
        local rsize

        if api.region_span then
            -- Offline harness seam: its regions are plain tables.
            rbase, rsize = api.region_span(region)
        else
            rbase = uptr(region.base)
            rsize = region.size
        end

        if state.at == nil then state.at = rbase end

        if state.at >= rbase + rsize then
            state.ri = state.ri + 1
            state.at = nil
        elseif rsize >= M.ALIGN then
            local remaining = rbase + rsize - state.at
            local chunk = math.min(chunk_limit, remaining, math.max(budget, 0))
            -- Keep chunks a whole number of pointers so the alignment the
            -- walk started with is never broken by its own chunking.
            chunk = chunk - (chunk % M.ALIGN)
            if chunk < M.ALIGN then
                state.ri = state.ri + 1
                state.at = nil
            else
                local blob = read_range(api, ffi.cast("uint8_t *", state.at), chunk)
                if blob == nil then
                    -- Unreadable: skip rather than stall on one bad page.
                    state.ri = state.ri + 1
                    state.at = nil
                else
                    local p32 = ffi.cast("uint32_t *", blob)
                    local last = chunk - M.ALIGN
                    for off = 0, last, M.ALIGN do
                        local idx = off / 4
                        -- Read the pointer as two uint32 halves rather than one
                        -- uint64. In LuaJIT a uint64_t index yields a CDATA
                        -- value, and cdata does not compare or hash equal to a
                        -- Lua number - so a table lookup keyed by the low half
                        -- would miss every time. uint32_t indexes yield real
                        -- Lua numbers, which is what the `wanted` table is
                        -- keyed by. (Measured, not assumed.)
                        local lo = p32[idx]
                        local bucket = state.wanted[lo]
                        if bucket ~= nil then
                            local hi = p32[idx + 1]
                            for _, cand in ipairs(bucket) do
                                if cand.high == hi and #state.hits < M.MAX_HITS then
                                    local address = state.at + off
                                    state.hits[#state.hits + 1] = {
                                        address = address,
                                        rva = address - uptr(state.module_base),
                                        name = cand.name,
                                        value = cand.high * 4294967296 + lo,
                                    }
                                end
                            end
                        end
                    end

                    state.scanned = state.scanned + chunk
                    state.at = state.at + chunk
                    budget = budget - chunk

                    if budget <= 0 and state.ri <= #state.regions then
                        return false, ("static route probe: scanned %d of %d bytes")
                            :format(state.scanned, state.total)
                    end
                end
            end
        else
            state.ri = state.ri + 1
            state.at = nil
        end
    end

    return true, nil
end

-- The lines worth writing to the log once a run finishes.
function M.describe_hits(state)
    local lines = {}
    local scanned_desc = ("%d byte(s) scanned (%d read-only, %d writable)")
        :format(state.scanned, state.readonly_bytes or 0, state.writable_bytes or 0)
    if #state.hits == 0 then
        lines[#lines + 1] = ("static route NOT FOUND: %s; no pointer to the table"
            ):format(scanned_desc)
        lines[#lines + 1] = "  (the table is reached some other way - the scan stays)"
    else
        lines[#lines + 1] = ("static route: %d pointer hit(s) in game.dll - %s")
            :format(#state.hits, scanned_desc)
        for _, h in ipairs(state.hits) do
            lines[#lines + 1] = ("  rva 0x%x points at %s (0x%x)")
                :format(h.rva, h.name, h.value)
        end
        lines[#lines + 1] = "  (the same rva on the next launch means a static route exists)"
    end
    return lines
end

-- ===========================================================================
-- Self-bootstrapping mode
--
-- The search above looks for a KNOWN address: the caller supplies `table_base`
-- and the scan finds the slots holding that exact value. That works when
-- something else has already located the table - which is exactly what is
-- unavailable after a game update, when the scan is the thing that stopped
-- working. So the probe could not run in the only situation it exists for.
--
-- This mode inverts it. It walks the module's writable data for values that
-- could be a heap pointer, then asks of each: does this point at something that
-- LOOKS LIKE the damage table? No prior knowledge of the address is needed.
--
-- Cost. The writable sections hold ~3.4 M aligned slots (26 MB). Testing every
-- one would mean ~0.7 GB of reads, so there is a cheap arithmetic pre-filter
-- first, and only survivors cost a read:
--
--   * value < 0x10000              - null-ish, small integer, not an address
--   * value inside the module image - a self-reference, and the table is on the
--                                     heap (module ~0x7ff..., heap ~0x1a...)
--   * value >= 0x800000000000      - kernel space
--
-- What survives is a few hundred slots at most in practice, each costing one
-- small read. That is the same order as the original probe's cost.
--
-- The identity test is deliberately weaker than the runtime's: the runtime
-- knows which row it wants and can check its neighbours, whereas this must
-- accept "a damage table" without knowing which weapon is being edited. So it
-- checks invariants that hold for EVERY row of the real table, measured rather
-- than assumed:
--
--   +0  type id     1..2000      (real range 1..649)
--   +4  damage      -1..100000   (-1 is a real sentinel value, 3 rows use it)
--   +8  durable     -1..100000
--   +12 AP[0]       0..20        (20 is real - a row uses it)
--   +28 forces      within the measured range (below)
--   +40 element     0..8
--
-- and then that 32 consecutive rows satisfy it, with mostly-distinct ids.
--
-- WHAT THIS CANNOT DO, stated plainly because the limit matters more than the
-- test: a synthetic buffer that fills in plausible values for every one of
-- these fields passes. There is no static check that distinguishes "the game's
-- damage table" from "32 records that look exactly like it" - that is a
-- property of provenance, not of bytes. A test asserting otherwise would be
-- asserting the impossible.
--
-- So the probe is not a decision procedure. It produces CANDIDATES, and the
-- thing that identifies the right one is comparing the rva across two launches:
-- the game's real pointer is at the same offset every run, whereas a
-- coincidence in a heap object that happens to be shaped like a table will not
-- repeat. That is why the output is a list of rvas to compare, not a single
-- answer. The invariants above exist to make the candidate list short, not to
-- make it certain.
-- ===========================================================================

M.CANDIDATE_ROWS = 32          -- rows that must satisfy the invariants
M.MIN_UNIQUE_RATIO = 0.8       -- ids must be mostly distinct
M.MAX_CANDIDATES = 64          -- stop after this many hits

-- Measured on the real table; see the comment above for why they are ranges
-- rather than exact sets. The forces are the tightest signal available: the
-- real table uses 14 distinct demolition values, 26 force_strength values and
-- 32 impulse values across 649 rows.
M.MAX_FORCE = 10000            -- real max is 8000 (force_impulse)
M.MAX_ELEMENT = 16             -- real max is 8

local function plausible_pointer(value, module_base, module_size)
    if value < 0x10000 then return false end
    if value >= 0x800000000000 then return false end
    local mb = uptr(module_base)
    if value >= mb and value < mb + module_size then return false end
    return true
end

-- Does the memory at `address` look like a damage table?
--
-- Returns ok, detail. Reads only what it must: the first row's id decides most
-- negatives, and a short read fails immediately.
function M.looks_like_table(api, address)
    local need = M.CANDIDATE_ROWS * M.RECORD_SIZE
    local blob = read_range(api, ffi.cast("uint8_t *", address), need)
    if blob == nil then
        return false, "unreadable"
    end
    local p32 = ffi.cast("uint32_t *", blob)
    local p_i32 = ffi.cast("int32_t *", blob)
    local ids = {}
    local seen = {}
    local unique = 0
    for i = 0, M.CANDIDATE_ROWS - 1 do
        local base = (i * M.RECORD_SIZE) / 4
        local tid = p_i32[base]
        if tid < 1 or tid > 2000 then
            return false, ("row %d: type id %d out of range"):format(i, tid)
        end
        local dmg = p_i32[base + 1]
        local dur = p_i32[base + 2]
        if dmg < -1 or dmg > 100000 or dur < -1 or dur > 100000 then
            return false, ("row %d: damage %d/%d out of range"):format(i, dmg, dur)
        end
        local ap0 = p32[base + 3]
        if ap0 > 20 then
            return false, ("row %d: ap %d out of range"):format(i, ap0)
        end
        -- Forces and element type: the tightest signals available. A heap
        -- object that happens to hold a run of small ints will usually fail
        -- here even when it passes the damage ranges.
        local dem = p32[base + 7]
        local fs = p32[base + 8]
        local fi = p32[base + 9]
        if dem > M.MAX_FORCE or fs > M.MAX_FORCE or fi > M.MAX_FORCE then
            return false, ("row %d: forces %d/%d/%d out of range"):format(i, dem, fs, fi)
        end
        local elem = p32[base + 10]
        if elem > M.MAX_ELEMENT then
            return false, ("row %d: element %d out of range"):format(i, elem)
        end
        ids[i + 1] = tid
        if not seen[tid] then seen[tid] = true unique = unique + 1 end
    end
    local ratio = unique / M.CANDIDATE_ROWS
    if ratio < M.MIN_UNIQUE_RATIO then
        return false, ("ids not distinct enough (%.2f)"):format(ratio)
    end
    return true, ("%d rows, %.2f unique"):format(M.CANDIDATE_ROWS, ratio)
end

-- Begin a self-bootstrapping run: no table_base required.
function M.begin_blind(api, module_base)
    local regions, readonly_bytes, writable_bytes
    if api.module_regions then
        regions, readonly_bytes, writable_bytes = api.module_regions(api, module_base)
    else
        regions, readonly_bytes, writable_bytes = M.module_regions(api, module_base)
    end

    local mb = uptr(module_base)
    -- The module's own span, to reject self-references.
    local module_size = 0
    for _, r in ipairs(regions) do
        local base, size
        if api.region_span then base, size = api.region_span(r)
        else base, size = uptr(r.base), r.size end
        local endp = base + size
        if endp - mb > module_size then module_size = endp - mb end
    end

    return {
        module_base = module_base,
        module_size = module_size,
        regions = regions,
        readonly_bytes = readonly_bytes or 0,
        writable_bytes = writable_bytes or 0,
        ri = 1,
        at = nil,
        hits = {},
        scanned = 0,
        tested = 0,
        skipped = 0,
        total = total_size(regions),
        blind = true,
    }
end

-- One budget's worth of the blind walk.
function M.step_blind(api, state)
    local budget = M.BUDGET
    local chunk_limit = M.CHUNK

    while state.ri <= #state.regions do
        local region = state.regions[state.ri]
        local rbase, rsize
        if api.region_span then rbase, rsize = api.region_span(region)
        else rbase, rsize = uptr(region.base), region.size end

        if state.at == nil then state.at = rbase end
        if state.at >= rbase + rsize then
            state.ri = state.ri + 1
            state.at = nil
        elseif rsize >= M.ALIGN then
            local remaining = rbase + rsize - state.at
            local chunk = math.min(chunk_limit, remaining, math.max(budget, 0))
            chunk = chunk - (chunk % M.ALIGN)
            if chunk < M.ALIGN then
                state.ri = state.ri + 1
                state.at = nil
            else
                local blob = read_range(api, ffi.cast("uint8_t *", state.at), chunk)
                if blob == nil then
                    state.ri = state.ri + 1
                    state.at = nil
                else
                    local p32 = ffi.cast("uint32_t *", blob)
                    local last = chunk - M.ALIGN
                    for off = 0, last, M.ALIGN do
                        local idx = off / 4
                        local lo = p32[idx]
                        local hi = p32[idx + 1]
                        local value = hi * 4294967296 + lo
                        if plausible_pointer(value, state.module_base, state.module_size) then
                            state.tested = state.tested + 1
                            local ok, detail = M.looks_like_table(api, value)
                            if ok and #state.hits < M.MAX_CANDIDATES then
                                local address = state.at + off
                                state.hits[#state.hits + 1] = {
                                    address = address,
                                    rva = address - uptr(state.module_base),
                                    name = "table-candidate",
                                    value = value,
                                    detail = detail,
                                }
                            elseif not ok then
                                state.skipped = state.skipped + 1
                            end
                        end
                    end
                    state.scanned = state.scanned + chunk
                    state.at = state.at + chunk
                    budget = budget - chunk
                    if budget <= 0 and state.ri <= #state.regions then
                        return false, ("blind probe: scanned %d of %d bytes, "
                            .. "%d candidate(s) tested"):format(
                            state.scanned, state.total, state.tested)
                    end
                end
            end
        else
            state.ri = state.ri + 1
            state.at = nil
        end
    end
    return true, nil
end

function M.describe_blind(state)
    local lines = {}
    lines[#lines + 1] = ("blind probe: %d byte(s) scanned, %d slot(s) looked like "
        .. "pointers, %d hit(s)"):format(state.scanned, state.tested, #state.hits)
    if #state.hits == 0 then
        lines[#lines + 1] = "  no pointer in game.dll points at anything table-shaped"
        lines[#lines + 1] = "  (the table may be reached through several hops)"
    else
        for _, h in ipairs(state.hits) do
            lines[#lines + 1] = ("  rva 0x%x -> 0x%x  (%s)")
                :format(h.rva, h.value, h.detail)
        end
        lines[#lines + 1] = "  (an rva that repeats across two launches is the route)"
    end
    return lines
end

return M
