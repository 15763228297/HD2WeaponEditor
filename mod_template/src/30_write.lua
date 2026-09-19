--[[
30_write.lua -- apply the change, then prove it landed.

Design rules, each one earned:

* **Write only what changed.** Re-writing untouched fields turns a no-op into a
  memory write, and every unnecessary write is another chance to be wrong.

* **Read back and verify.** A write call returning success is not evidence the
  value is there; nothing between here and the page is cooperative. If the
  read-back disagrees, that is a failure and must be reported, not swallowed.

* **Never retry a failed write.** A second attempt at an address that did not
  accept the first one has the same information as the first attempt. Retrying
  only adds another chance of writing somewhere wrong.

* **Failures are returned, never thrown.** The caller decides how loud to be; a
  crash inside a mission would be worse than a missing feature.
]]

local M = { VERSION = "write-v1" }

-- The guard module is injected rather than required, matching how the resolver
-- takes its API: the generated mod assembles the pieces explicitly, so the same
-- files can be driven by the offline harness without a module system.
function M.init(guard)
    M.guard = guard
    return M
end

-- Encode a uint32 the way the record stores it.
local function u32(value)
    local ffi = require("ffi")
    local buffer = ffi.new("uint32_t[1]")
    buffer[0] = value
    return buffer
end

-- Write one uint32 through the API's write seam.
--
-- Two seams, mirroring the resolver: `api.write_bytes(address, buffer, size)`
-- for the offline harness, or the real `kernel.WriteProcessMemory`.
local function write_u32(api, address, value)
    local buffer = u32(value)
    local size = 4

    if api.write then
        return api.write(address, buffer, size)
    end

    local ffi = require("ffi")
    local written = ffi.new("size_t[1]")
    local ok = api.kernel.WriteProcessMemory(
        api.process, address, buffer, size, written
    )
    if ok == 0 then return false, "WriteProcessMemory failed" end
    if tonumber(written[0]) ~= size then
        return false, ("wrote %d of %d bytes"):format(tonumber(written[0]), size)
    end
    return true
end

M.write_u32 = write_u32

-- Field order is fixed so the log reads the same way every run, and so the
-- read-back loop below has a deterministic list to walk.
local WRITE_ORDER = { "damage", "durable", "ap0", "ap1", "ap2", "ap3" }

-- Field-name to offset mapping lives in the guard module, which owns the
-- vocabulary shared by the guard, the writer and the generator. Duplicating it
-- here is how the two drifted apart once already: the guard knew `ap` but not
-- `ap0`, so a clean run refused to write.
local function field_offset(resolver, field)
    return M.guard.field_offset(resolver, field)
end

-- Apply `changes` at `address`. The caller is expected to have run the guards
-- already; this function does not re-check identity, but it does verify that
-- each write landed.
--
-- Returns ok, applied (list of {field, offset, from, to}), problems (list of
-- strings). `from` is read before writing, so the log can show the transition.
function M.apply(api, resolver, address, changes)
    local applied = {}
    local problems = {}
    local size = resolver.RECORD_SIZE

    -- Snapshot once: the read-back compares against this buffer's neighbour
    -- fields, and reading twice would allow a torn view.
    local before = resolver.read_range(api, address, size)
    if before == nil then
        return false, applied, { "could not read the record before writing" }
    end

    for _, field in ipairs(WRITE_ORDER) do
        local target = changes[field]
        if target ~= nil then
            local offset = field_offset(resolver, field)
            if offset == nil then
                problems[#problems + 1] = ("no offset known for field %s"):format(field)
            else
                local from = resolver.u32_at(before, offset)
                if from == target then
                    -- Already there. Writing it again would be a no-op that
                    -- still counts as a memory write, so skip it and say so.
                    applied[#applied + 1] = {
                        field = field, offset = offset, from = from, to = target,
                        unchanged = true,
                    }
                else
                    local field_address = address + offset
                    local ok, why = write_u32(api, field_address, target)
                    if not ok then
                        problems[#problems + 1] =
                            ("%s: write failed (%s)"):format(field, tostring(why))
                    else
                        -- Read this field back immediately rather than trusting
                        -- the write call. A page can accept a write and still
                        -- not hold the value if something else owns it.
                        local check = resolver.read_range(api, field_address, 4)
                        if check == nil then
                            problems[#problems + 1] =
                                ("%s: written but could not read back"):format(field)
                        else
                            local now = resolver.u32_at(check, 0)
                            if now ~= target then
                                problems[#problems + 1] =
                                    ("%s: wrote %d but read back %d"):format(field, target, now)
                            else
                                applied[#applied + 1] = {
                                    field = field, offset = offset,
                                    from = from, to = target,
                                }
                            end
                        end
                    end
                end
            end
        end
    end

    return #problems == 0, applied, problems
end

-- Re-read the whole record and confirm every requested field now holds its
-- target. This is the end-to-end check: it does not care how the write was
-- done, only whether the memory now says what it should.
function M.verify(api, resolver, address, changes)
    local buffer = resolver.read_range(api, address, resolver.RECORD_SIZE)
    if buffer == nil then
        return false, { "could not read the record to verify" }
    end

    local bad = {}
    for _, field in ipairs(WRITE_ORDER) do
        local target = changes[field]
        if target ~= nil then
            local offset = field_offset(resolver, field)
            if offset == nil then
                bad[#bad + 1] = ("%s: no known offset"):format(field)
            else
                local now = resolver.u32_at(buffer, offset)
                if now ~= target then
                    bad[#bad + 1] = ("%s is %d, expected %d"):format(field, now, target)
                end
            end
        end
    end

    if #bad > 0 then return false, bad end
    return true, nil
end

-- Describe the applied changes for the log.
function M.describe(applied)
    local parts = {}
    for _, item in ipairs(applied) do
        if item.unchanged then
            parts[#parts + 1] = ("%s=%d (already set)"):format(item.field, item.to)
        else
            parts[#parts + 1] = ("%s %d->%d"):format(item.field, item.from, item.to)
        end
    end
    return table.concat(parts, ", ")
end

return M
