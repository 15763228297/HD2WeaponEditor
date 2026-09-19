--[[
 Offline driver for 10_resolver.lua.

 Loads the real resolver file, injects a fake memory API backed by
 data/sim_memory.bin, and runs find_record on the R-4 row.

 Running the shipped resolver against a simulated address space is the only way
 to develop address-scanning code safely: the failure mode being guarded against
 is writing to a wrong address, and that cannot be iterated on live.

 Usage: luajit driver_resolver.lua <sim_memory.bin> <array_base> <count>
]]

local ffi = require("ffi")

local sim_path  = arg[1]
local array_base = tonumber(arg[2])
local count      = tonumber(arg[3])

-- ---------------------------------------------------------------------------
-- Load the simulated address space.
-- ---------------------------------------------------------------------------
local file = assert(io.open(sim_path, "rb"))
local image = file:read("*a")
file:close()

local IMAGE_BASE = 0x10000000
local image_len = #image

-- ---------------------------------------------------------------------------
-- The fake API the resolver will use.
-- ---------------------------------------------------------------------------
local fake = {
    MEM_COMMIT = 0x1000,
    PAGE_GUARD = 0x100,
    WRITABLE = 0x04,
    WRITABLE2 = 0x40,
    WRITABLE3 = 0x08,
}

local reads, bytes_read, max_read = 0, 0, 0

function fake.read_range(address, size)
    local offset = tonumber(ffi.cast("uintptr_t", address)) - IMAGE_BASE
    if offset < 0 or offset + size > image_len then return nil end
    local buffer = ffi.new("uint8_t[?]", size)
    ffi.copy(buffer, image:sub(offset + 1, offset + size), size)
    reads = reads + 1
    bytes_read = bytes_read + size
    if size > max_read then max_read = size end
    return buffer
end

-- One writable region covering the whole image.
function fake.writable_regions(_api, start, limit)
    local start_u = tonumber(ffi.cast("uintptr_t", start))
    local limit_u = tonumber(ffi.cast("uintptr_t", limit))
    if start_u >= IMAGE_BASE + image_len or limit_u <= IMAGE_BASE then
        return {}
    end
    return { { base = ffi.cast("uint8_t *", IMAGE_BASE), size = image_len } }
end

-- ---------------------------------------------------------------------------
-- Load the resolver under test.
-- ---------------------------------------------------------------------------
rawset(_G, "__resolver_api", fake)

local resolver_path = arg[4] or "mod_template/src/10_resolver.lua"
local chunk = assert(loadfile(resolver_path))
local resolver = chunk()

-- ---------------------------------------------------------------------------
-- Run it.
-- ---------------------------------------------------------------------------
-- R-4 Hyena: damage[137] = 220/45 AP[3,3,3,0]; neighbours are IDs 136 and 138.
local record = {
    index = 137, damage = 220, durable = 45,
    ap = { 3, 3, 3, 0 },
}
local expect = {
    before_id = 136,
    after_id = 138,
    next = { damage = 200, durable = 50 },   -- damage[138]
}

local ok, address, near = resolver.find_record(fake, IMAGE_BASE, record, expect)

print("RESULT " .. tostring(ok))
if ok then
    local offset = tonumber(ffi.cast("uintptr_t", address)) - IMAGE_BASE
    print("ADDRESS " .. tostring(offset))
    print("EXPECTED " .. tostring(array_base + 137 * 76))
    print("BEFORE " .. tostring(near.before.index) .. " " .. tostring(near.before.damage))
    print("AFTER " .. tostring(near.after.index) .. " " .. tostring(near.after.damage))
else
    print("REASON " .. tostring(address))
end
print("READS " .. reads)
print("BYTES " .. bytes_read)
print("MAXREAD " .. max_read)

-- ---------------------------------------------------------------------------
-- Negative controls: the checks must reject these.
-- ---------------------------------------------------------------------------
-- 1. Wrong neighbour IDs (as if we located a different row).
local ok2, reason2 = resolver.find_record(fake, IMAGE_BASE, record, {
    before_id = 999, after_id = 999, next = { damage = 200, durable = 50 },
})
print("WRONG_NEIGHBOURS " .. tostring(ok2))

-- 2. A record whose values do not exist anywhere in the table.
local ok3, reason3 = resolver.find_record(fake, IMAGE_BASE, {
    index = 137, damage = 99999, durable = 1, ap = { 9, 9, 9, 9 },
}, expect)
print("ABSENT_RECORD " .. tostring(ok3))

-- 3. Right record, but the next row contradicts what the table says it holds.
local ok4, reason4 = resolver.find_record(fake, IMAGE_BASE, record, {
    before_id = 136, after_id = 138, next = { damage = 11111, durable = 22222 },
})
print("WRONG_NEXT " .. tostring(ok4))
