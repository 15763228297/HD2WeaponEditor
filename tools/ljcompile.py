"""Compile Lua source to the exact LuaJIT bytecode resource the game loads.

The game ships its own LuaJIT runtime at <game>/bin/lua51.dll
("LuaJIT 2.1.0-alpha", 138 exports incl. luaL_loadbuffer/lua_dump/luaopen_ffi),
so no external luajit.exe is needed: we drive that DLL through ctypes.

Usage:
    python tools/ljcompile.py <input.lua> <output.lua.main>
    python tools/ljcompile.py --check        # self-test against a known chunk
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import struct
import sys
from pathlib import Path

LUA_OK = 0
LUA_MULTRET = -1
LUA_TSTRING = 4
LUA_TTABLE = 5
LUA_TFUNCTION = 6
LUA_GLOBALSINDEX = -10002

# Helldivers 2's Steam app id. Used to find the install through Steam's own
# metadata rather than guessing at paths.
HD2_APPID = "553850"

# Where the game is, relative to a Steam library root.
GAME_SUBPATH = ("steamapps", "common", "Helldivers 2", "bin", "lua51.dll")

# Last-resort guesses, kept for machines with no readable Steam metadata.
DEFAULT_DLL_CANDIDATES = [
    os.path.expandvars(r"%PROGRAMFILES(X86)%\Steam\steamapps\common\Helldivers 2\bin\lua51.dll"),
    r"C:\Program Files (x86)\Steam\steamapps\common\Helldivers 2\bin\lua51.dll",
]


def config_path() -> Path:
    """Where a user's manual lua51.dll path is remembered.

    A packaged exe has no command line, so `HD2_LUA_DLL` and `--dll` are
    unreachable for the people most likely to need them. A file next to the
    executable (or in the user profile when that is not writable) gives them an
    escape hatch that does not require editing the tool.
    """
    if getattr(sys, "frozen", False):
        beside = Path(sys.executable).resolve().parent / "hd2editor.json"
        if os.access(beside.parent, os.W_OK):
            return beside
    return Path.home() / ".hd2weaponeditor.json"


def load_config() -> dict:
    """Read the optional config file. Any problem yields {} - never raises."""
    try:
        path = config_path()
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        pass
    return {}


def _config_lua_dll() -> str | None:
    value = load_config().get("lua51_dll")
    return value if isinstance(value, str) and value else None


def _steam_roots() -> list[str]:
    """Every Steam library root on this machine.

    Steam does not keep the game in the Steam install directory - it lives in
    whichever library the user chose, which may be on another drive entirely.
    A hardcoded list of "typical" paths therefore misses most real installs:
    the first user to hit this had the game on a library the tool never looked
    at, and got "lua51.dll not found" from a tool that had shipped a path from
    the developer's own disk.

    Sources, in order of reliability:
      1. libraryfolders.vdf under the Steam install - the authoritative list
      2. the Steam install directory itself, and its default library
      3. every drive letter, for a library Steam was not asked about
    """
    roots: list[str] = []
    seen: set[str] = set()

    def add(path: str | None) -> None:
        if not path:
            return
        norm = os.path.normpath(path)
        key = norm.lower()
        if key not in seen and os.path.isdir(norm):
            seen.add(key)
            roots.append(norm)

    steam_dirs: list[str] = []

    # 1. Registry: where Steam itself is installed.
    if os.name == "nt":
        try:
            import winreg
            for hive, key in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
            ):
                try:
                    with winreg.OpenKey(hive, key) as handle:
                        for value in ("SteamPath", "InstallPath"):
                            try:
                                raw, _ = winreg.QueryValueEx(handle, value)
                                if raw:
                                    steam_dirs.append(str(raw))
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass

    # 2. Steam's own library list - this is what catches other drives.
    for steam in list(steam_dirs):
        add(steam)
        vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
        for library in _parse_library_folders(vdf):
            add(library)

    # 3. Every drive letter, so a library Steam was never asked about is still
    #    reachable. Cheap: a few os.path.isdir calls.
    if os.name == "nt":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            for pattern in (
                rf"{letter}:\SteamLibrary",
                rf"{letter}:\Program Files (x86)\Steam",
                rf"{letter}:\Program Files\Steam",
                rf"{letter}:\Steam",
                rf"{letter}:\Games\Steam",
            ):
                add(pattern)

    return roots


def _parse_library_folders(vdf_path: str) -> list[str]:
    """Pull library paths out of Steam's libraryfolders.vdf.

    Parsed with a regex rather than a VDF library: the file only ever needs one
    field read from it, and a dependency-free parse keeps the packaged exe
    small and the failure mode obvious. Handles the escaped backslashes Steam
    writes (``"D:\\\\Program Files (x86)\\\\Steam"``).
    """
    try:
        with open(vdf_path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return []

    libraries = []
    for match in re.finditer(r'"path"\s*"([^"]+)"', text):
        raw = match.group(1).replace("\\\\", "\\")
        libraries.append(raw)
    return libraries


def _candidate_dll_paths() -> list[str]:
    """Every plausible lua51.dll location, best guess first.

    Deduplicated: the fallback list overlaps with the discovered Steam roots on
    a default install, and a path listed twice makes the failure message look
    like it searched more places than it did.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        norm = os.path.normpath(path)
        key = norm.lower()
        if key not in seen:
            seen.add(key)
            candidates.append(norm)

    for root in _steam_roots():
        add(os.path.join(root, *GAME_SUBPATH))

    for fallback in DEFAULT_DLL_CANDIDATES:
        add(fallback)

    return candidates


def find_lua_dll(explicit: str | None = None) -> str:
    """Locate the game's lua51.dll, or raise with something actionable.

    Order: an explicit argument (or HD2_LUA_DLL), then a path the user saved in
    the config file, then Steam's own metadata.

    The error message matters as much as the search: the previous one told the
    user to "pass --dll or set HD2_LUA_DLL", which is impossible advice for
    someone running a packaged exe with no command line. A GUI user needs to be
    told where the tool looked and what to do about it, in the interface.
    """
    searched: list[str] = []

    explicit = explicit or os.environ.get("HD2_LUA_DLL") or _config_lua_dll()
    if explicit:
        if os.path.isfile(explicit):
            return explicit
        searched.append(explicit)

    for candidate in _candidate_dll_paths():
        if os.path.isfile(candidate):
            return candidate
        searched.append(candidate)

    raise LuaDllNotFound(searched)


class LuaDllNotFound(FileNotFoundError):
    """Raised when the game's LuaJIT runtime cannot be located.

    Carries the paths that were tried so the UI can show them, and so a user
    can tell the difference between "the game is somewhere unusual" and "the
    game is not installed on this machine at all".
    """

    def __init__(self, searched: list[str]):
        self.searched = searched
        roots = _steam_roots()
        detail = [
            "找不到游戏的 lua51.dll，无法编译 Mod。",
            "",
            "本工具需要游戏自带的 LuaJIT 来把 Lua 编译成游戏能读的字节码，",
            "所以必须能读到你安装《绝地潜兵 2》的位置。",
            "",
            "已查找以下位置：",
        ]
        for path in searched[:12]:
            detail.append(f"  {path}")
        if len(searched) > 12:
            detail.append(f"  ...（共 {len(searched)} 处）")
        if roots:
            detail.append("")
            detail.append("检测到的 Steam 库：")
            for root in roots[:8]:
                detail.append(f"  {root}")
        detail.append("")
        detail.append("如果你确认游戏已安装，请把这段信息发给作者。")
        super().__init__("\n".join(detail))


class LuaJIT:
    """Minimal binding: enough to compile and strip-dump a chunk."""

    def __init__(self, dll_path: str | None = None):
        # find_lua_dll already consults HD2_LUA_DLL and the config file, so
        # passing the environment value here as well would just duplicate that
        # order and make the precedence harder to reason about.
        self.path = find_lua_dll(dll_path)
        # Preload lua51.dll with the game's bin dir on the DLL search path, so
        # the runtime's own imports (MSVCR110 for this build) resolve. Without
        # this, luaL_newstate returns NULL inside a frozen process and the only
        # symptom is a bare MemoryError.
        #
        # The directory is added only for the lifetime of this call and is NOT
        # left on PATH: appending it to PATH broke string.dump, and a stale
        # entry would affect unrelated loads.
        self._cookie = None
        game_bin = os.path.dirname(self.path)
        if game_bin and os.path.isdir(game_bin):
            try:
                self._cookie = os.add_dll_directory(game_bin)
            except (AttributeError, OSError):
                self._cookie = None
        self.lib = ctypes.CDLL(self.path, winmode=0)
        self._bind()

    def _bind(self) -> None:
        lib = self.lib
        lib.luaL_newstate.restype = ctypes.c_void_p
        lib.luaL_openlibs.argtypes = [ctypes.c_void_p]
        lib.luaL_loadbuffer.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                        ctypes.c_size_t, ctypes.c_char_p]
        lib.luaL_loadbuffer.restype = ctypes.c_int
        lib.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        lib.lua_pcall.restype = ctypes.c_int
        lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_size_t)]
        lib.lua_tolstring.restype = ctypes.c_char_p
        lib.lua_getfield.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p]
        lib.lua_pushboolean.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.lua_settop.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.lua_close.argtypes = [ctypes.c_void_p]
        lib.lua_type.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.lua_type.restype = ctypes.c_int
        lib.lua_gc.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]

    def __enter__(self) -> "LuaJIT":
        self.state = self.lib.luaL_newstate()
        if not self.state:
            raise MemoryError("luaL_newstate failed")
        self.lib.luaL_openlibs(self.state)
        return self

    def __exit__(self, *exc) -> None:
        if getattr(self, "state", None):
            self.lib.lua_close(self.state)
            self.state = None

    def _error(self, what: str) -> str:
        size = ctypes.c_size_t()
        raw = self.lib.lua_tolstring(self.state, -1, ctypes.byref(size))
        message = raw.decode("utf-8", "replace") if raw else "(no message)"
        self.lib.lua_settop(self.state, -2)
        return f"{what}: {message}"

    def load(self, source: bytes, chunkname: str = "=chunk") -> None:
        status = self.lib.luaL_loadbuffer(self.state, source, len(source),
                                          chunkname.encode())
        if status != LUA_OK:
            raise SyntaxError(self._error("load failed"))

    def dump_stripped(self) -> bytes:
        """string.dump(chunk, true) -> stripped LuaJIT bytecode.

        The chunk is dumped and copied entirely inside Lua (string.dump plus a
        byte loop building a hex string), then that ASCII hex is read across the
        C boundary. Doing it this way matters: marshalling the dumped string
        straight through the FFI pointer produced bytecode the game's own
        LuaJIT rejected with "cannot load malformed bytecode", and because every
        test ran the *source* rather than the shipped bytes, nothing caught it.
        """
        lib = self.lib
        program = (
            "local f = ...\n"
            "local s = string.dump(f, true)\n"
            "local t = {}\n"
            "for i = 1, #s do t[i] = string.format('%02x', s:byte(i)) end\n"
            "return table.concat(t)\n"
        )
        lib.luaL_loadstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.luaL_loadstring.restype = ctypes.c_int
        lib.lua_pushvalue.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        lib.lua_pcall.restype = ctypes.c_int
        lib.lua_settop.argtypes = [ctypes.c_void_p, ctypes.c_int]

        if lib.luaL_loadstring(self.state, program.encode()) != LUA_OK:
            raise RuntimeError(self._error("dump helper failed to compile"))
        lib.lua_pushvalue(self.state, -2)          # pass the chunk as an argument
        status = lib.lua_pcall(self.state, 1, 1, 0)
        if status != LUA_OK:
            raise RuntimeError(self._error("string.dump failed"))
        size = ctypes.c_size_t()
        raw = lib.lua_tolstring(self.state, -1, ctypes.byref(size))
        if raw is None:
            raise RuntimeError("string.dump returned a non-string")
        hex_text = ctypes.string_at(raw, size.value).decode("ascii")
        lib.lua_settop(self.state, -2)             # drop the hex, keep the chunk
        return bytes.fromhex(hex_text)


def compile_source(source: str, dll_path: str | None = None,
                   chunkname: str = "=mod") -> bytes:
    """Return the LuaJIT bytecode for `source` (stripped).

    Runs the compile in a CHILD PROCESS whenever that is possible, because
    LuaJIT needs a 2 GB-aligned block of address space on x64 and cannot get
    one once a WebView2/Chromium runtime has reserved most of it: in the
    desktop build, luaL_newstate() returns NULL after the window opens, and the
    only symptom is a bare MemoryError. A child process starts with a clean
    address space and is unaffected.

    Falls back to compiling in-process when a child cannot be spawned (frozen
    builds without an interpreter, or when already inside a child) - that path
    works everywhere except after a WebView has opened.
    """
    if not os.environ.get("_HD2_LUA_CHILD"):
        child = _compile_in_child(source, dll_path, chunkname)
        if child is not None:
            return child
    with LuaJIT(dll_path) as runtime:
        runtime.load(source.encode("utf-8"), chunkname)
        return runtime.dump_stripped()


COMPILE_FLAG = "--compile-stdin"


def _as_compiler() -> int:
    """Run as a one-shot compiler: Lua on stdin, bytecode on stdout.

    The desktop app cannot compile in its own process once a WebView2 runtime
    has started (see compile_source). A frozen PyInstaller exe ignores `-c`, so
    instead the SAME executable is re-invoked with this flag: it reads the
    source from stdin, writes bytecode to stdout, and exits.
    """
    try:
        source = sys.stdin.buffer.read().decode("utf-8")
        out = compile_source(source, os.environ.get("HD2_LUA_DLL"), "=mod")
        sys.stdout.buffer.write(out)
        sys.stdout.buffer.flush()
        return 0
    except Exception as exc:
        sys.stderr.write("%s: %s" % (type(exc).__name__, exc))
        sys.stderr.write(chr(10))
        return 1


def _compile_in_child(source: str, dll_path: str | None,
                      chunkname: str) -> bytes | None:
    """Compile in a fresh process; return None if that is not possible.

    A child is what makes this work at all: LuaJIT needs a 2 GB-aligned block
    of address space on x64, and a WebView2/Chromium runtime in the same
    process has already reserved enough of it that luaL_newstate() returns
    NULL. A freshly spawned process starts with a clean address space.
    """
    import subprocess

    here = os.path.dirname(os.path.abspath(__file__))
    env = dict(os.environ)
    env["_HD2_LUA_CHILD"] = "1"

    if getattr(sys, "frozen", False):
        # Frozen: a PyInstaller exe ignores `-c`, so re-invoke ITSELF in
        # compiler mode rather than trying to hand it a script.
        cmd = [sys.executable, COMPILE_FLAG]
    else:
        NL = "\n"
        script = (
            "import os, sys" + NL
            + "sys.path.insert(0, " + repr(here) + ")" + NL
            + "import ljcompile" + NL
            + "src = sys.stdin.buffer.read().decode('utf-8')" + NL
            + "out = ljcompile.compile_source(src, "
            + repr(dll_path) + ", " + repr(chunkname) + ")" + NL
            + "sys.stdout.buffer.write(out)" + NL
        )
        cmd = [sys.executable, "-c", script]

    try:
        proc = subprocess.run(
            cmd, input=source.encode("utf-8"),
            capture_output=True, timeout=180, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    return proc.stdout



def wrap_resource(bytecode: bytes) -> bytes:
    """8-byte resource header + bytecode, as stored inside the patch archive."""
    if bytecode[:4] != b"\x1bLJ\x02":
        raise ValueError(f"unexpected bytecode magic {bytecode[:4]!r} "
                         "(want stripped LuaJIT 2.1 bytecode)")
    return struct.pack("<II", len(bytecode), 2) + bytecode


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?")
    parser.add_argument("output", nargs="?")
    parser.add_argument("--dll", help="path to the game's lua51.dll")
    parser.add_argument("--check", action="store_true",
                        help="compile a probe chunk and report the header bytes")
    args = parser.parse_args()

    if args.check or not args.input:
        probe = "local t = {1,2,3}\nreturn #t\n"
        bytecode = compile_source(probe, args.dll)
        header = bytecode[:8]
        print(f"lua51.dll      : {find_lua_dll(args.dll)}")
        print(f"bytecode bytes : {len(bytecode)}")
        print(f"header         : {header.hex(' ')}")
        print(f"stripped       : {bool(header[4] & 0x02)}")
        print(f"resource bytes : {len(wrap_resource(bytecode))}")
        return 0 if header[:4] == b"\x1bLJ\x02" and header[4] & 0x02 else 1

    with open(args.input, encoding="utf-8") as handle:
        source = handle.read()
    bytecode = compile_source(source, args.dll)
    resource = wrap_resource(bytecode)
    if args.output:
        with open(args.output, "wb") as handle:
            handle.write(resource)
        print(f"{args.input} -> {args.output} "
              f"({len(bytecode)} bytes bytecode, {len(resource)} bytes resource)")
    else:
        sys.stdout.buffer.write(resource)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
