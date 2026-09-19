"""The log module must survive a handle it cannot flush.

The shared loader's `open_log` returns a handle with no `flush` method. An
earlier version of the log module wrapped `write` and `flush` in a single pcall,
so `flush` raised, the whole block aborted, and the message was lost - while the
file itself had already been created and truncated by the open.

That failure is invisible in the worst way: the log file exists, so the mod looks
installed; it is empty, so the mod looks like it never ran. Both conclusions are
wrong. It ran, it just could not speak.

This test drives the shipped log module with handles of each shape.

Run:  python tests/test_log_handles.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

import test_resolver as base_mod  # noqa: E402


def run_log_case(handle_kind: str) -> dict:
    """Load the log module with a stub CowboyBingusModLoader, then log twice."""
    source = (ROOT / "mod_template" / "src" / "40_log.lua").read_text(encoding="utf-8")

    probe = f"""
local ffi = require("ffi")
local out = {{}}

-- A handle shape that matches the shared loader: write works, flush does NOT
-- exist. This is the case that used to lose every line.
local function make_handle(kind)
  local h = {{ lines = {{}} }}
  function h.write(self, text)
    self.lines[#self.lines + 1] = text
    return self
  end
  if kind == "with_flush" then
    function h.flush(self) self.flushed = (self.flushed or 0) + 1 end
  end
  if kind == "write_throws" then
    function h.write(self, text) error("disk full") end
  end
  return h
end

local HANDLE = make_handle({handle_kind!r})

rawset(_G, "CowboyBingusModLoader", {{
  version = 15, api = 1, modules = {{}},
  open_log = function(name)
    out.name = name
    return HANDLE
  end,
}})

local log = (function()
  local s = [====[
{source}
]====]
  return assert(loadstring(s, "40_log"))()
end)()

log.line("first message")
log.line("second message")
log.close()

out.count = #HANDLE.lines
out.all = table.concat(HANDLE.lines, "")
out.flushed = HANDLE.flushed or 0
return table.concat({{
  "count=" .. tostring(out.count),
  "has_first=" .. tostring(out.all:find("first message") ~= nil),
  "has_second=" .. tostring(out.all:find("second message") ~= nil),
  "flushed=" .. tostring(out.flushed),
}}, ";")
"""
    raw = base_mod.run_probe(probe)
    result: dict = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if v == "true":
            result[k] = True
        elif v == "false":
            result[k] = False
        elif v.lstrip("-").isdigit():
            result[k] = int(v)
        else:
            result[k] = v
    return result


def main() -> int:
    check = base_mod.check

    print("== a handle with write but NO flush (what the loader returns) ==")
    out = run_log_case("no_flush")
    check("the messages were written, not lost", out.get("count", 0) >= 2,
          f"got {out.get('count')} lines")
    check("first line present", out.get("has_first") is True)
    check("second line present", out.get("has_second") is True)

    print()
    print("== a handle that has flush ==")
    out2 = run_log_case("with_flush")
    check("messages still written", out2.get("count", 0) >= 2, f"got {out2.get('count')}")
    check("flush was called", out2.get("flushed", 0) > 0, f"got {out2.get('flushed')}")

    print()
    print("== a handle whose write throws ==")
    # Must not raise out of log.line: a broken log cannot be allowed to abort
    # startup. Nothing to assert about content, only that we got here at all.
    out3 = run_log_case("write_throws")
    check("a throwing write does not propagate", True,
          f"count={out3.get('count')}")

    print()
    print(f"test_log_handles: "
          f"{'PASS' if not base_mod.failures else 'FAIL'} "
          f"({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
