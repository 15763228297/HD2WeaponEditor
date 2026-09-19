--[[
40_log.lua -- where the mod reports what it did and what it refused to do.

The log matters more here than in an ordinary mod. This one writes to game
memory, and when it declines to write the reason is the whole product: "baseline
mismatch on damage" tells the user a conflict happened and nothing was changed,
where silence would look like the mod failing for no reason.

Routes, in order of preference:

  1. `_G.CowboyBingusModLoader.open_log` - the shared loader publishes this and
     all CowboyBingus mods write through it, so our file lands beside theirs in
     %LOCALAPPDATA%/CowboyBingus/Helldivers2/Logs.
  2. `io.open` into that same directory, created if needed.
  3. `print`, so the game console shows something when there is no filesystem.

The loader API is checked at call time rather than cached at load time, because
load order between us and the loader is not guaranteed.
]]

local M = { VERSION = "log-v1" }

M.LOG_NAME = "WeaponEditor.log"

local file_handle = nil
local opened = false

local function timestamp()
    return os.date("%Y-%m-%d %H:%M:%S")
end

-- Ask the shared loader for a handle. Returns nil when the loader is absent.
local function try_loader()
    local compat = rawget(_G, "CowboyBingusModLoader")
    if type(compat) ~= "table" then return nil end
    if type(compat.open_log) ~= "function" then return nil end
    local ok, handle = pcall(compat.open_log, M.LOG_NAME)
    if ok and handle ~= nil then return handle end
    return nil
end

-- Create the log directory and open the file ourselves.
local function try_direct()
    local base = os.getenv("LOCALAPPDATA")
    if base == nil or base == "" then return nil end

    local path = base
    local ok = pcall(function()
        local ffi = require("ffi")
        pcall(ffi.cdef, [[
            int CreateDirectoryA(const char *path, void *security);
        ]])
        local kernel = ffi.load("kernel32")
        local parts = { "CowboyBingus", "Helldivers2", "Logs" }
        for _, part in ipairs(parts) do
            path = path .. "\\" .. part
            -- 183 = ERROR_ALREADY_EXISTS, which is success for our purpose.
            kernel.CreateDirectoryA(path, nil)
        end
    end)
    if not ok then return nil end

    local handle = io.open(path .. "\\" .. M.LOG_NAME, "w")
    return handle
end

local function handle()
    if opened then return file_handle end
    opened = true
    file_handle = try_loader() or try_direct()
    if file_handle ~= nil then
        M.raw_write(file_handle, ("WeaponEditor %s\n"):format(M.VERSION))
        M.raw_write(file_handle, ("started %s\n"):format(timestamp()))
    end
    return file_handle
end

M.handle = handle

-- Write one string to a handle, then flush if it can.
--
-- The two steps are deliberately separate. An earlier version wrapped
-- `write` and `flush` in one pcall, and the handle returned by the shared
-- loader's `open_log` has no `flush` method - so `flush` raised, the pcall
-- aborted the whole block, and the write was lost. The result was a log file
-- that existed, was being opened on every game launch (so it got truncated to
-- zero bytes), and never contained a single line. Silence that looks like the
-- mod never ran.
--
-- A reader that cannot be flushed is not a reason to drop the message: on a
-- real file the write lands in the buffer and reaches disk on close.
function M.raw_write(target, text)
    local wrote = pcall(function() target:write(text) end)
    if not wrote then
        return false, "write failed"
    end
    -- Flush when available; a missing flush is normal for some handles.
    if type(target.flush) == "function" then
        pcall(function() target:flush() end)
    end
    return true
end

-- Write one line. Never raises: a logging failure must not break the mod.
--
-- Every line also goes to `print`, which the game console shows. The file is the
-- durable record, but when the handle is unavailable or has gone bad, the
-- console is the only channel left - and a mod that writes to memory must never
-- be silent about what it did or refused to do.
function M.line(text)
    local message = ("%s  %s"):format(timestamp(), text)
    local target = handle()
    if target ~= nil then
        M.raw_write(target, message .. "\n")
    end
    pcall(print, "[WeaponEditor] " .. text)
end

function M.section(title)
    M.line("")
    M.line("== " .. title .. " ==")
end

-- Report a guard refusal. The detail may be a string or a list of
-- {field, detail} entries from the baseline check.
function M.refusal(code, detail)
    M.line("REFUSED: " .. code)
    if type(detail) == "table" then
        for _, item in ipairs(detail) do
            if type(item) == "table" then
                M.line(("  %s: %s"):format(tostring(item.field), tostring(item.detail)))
            else
                M.line("  " .. tostring(item))
            end
        end
    elseif detail ~= nil then
        M.line("  " .. tostring(detail))
    end
    M.line("  no change was applied")
end

function M.close()
    if file_handle ~= nil then
        M.raw_write(file_handle, ("stopped %s\n"):format(timestamp()))
        -- Same split as raw_write: closing may not exist on every handle, and a
        -- missing close must not undo the writes above.
        pcall(function() file_handle:close() end)
        file_handle = nil
    end
end

return M
