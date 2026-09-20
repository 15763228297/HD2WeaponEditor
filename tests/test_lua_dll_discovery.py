"""The game's lua51.dll must be found wherever Steam actually put it.

What this guards: the first user to run the packaged exe got

    FileNotFoundError: lua51.dll not found; pass --dll or set HD2_LUA_DLL

from a tool that only looked at three hardcoded paths - one of which was the
developer's own D: drive. Steam puts the game in whichever library the user
chose, which is frequently another drive, so a fixed list cannot work. The
message was also unactionable: it told someone running a GUI exe to "pass --dll",
which they cannot do.

So two properties are asserted:

  1. discovery reads Steam's OWN metadata (registry + libraryfolders.vdf), so a
     library on any drive is found;
  2. when nothing is found, the failure names every path tried and lists the
     Steam libraries detected, in the language the user reads.

Run:  python tests/test_lua_dll_discovery.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import ljcompile  # noqa: E402

failures = 0
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global failures, checks
    checks += 1
    if cond:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label} {detail}")
        failures += 1


def test_vdf_parsing() -> None:
    """libraryfolders.vdf is where Steam records libraries on other drives."""
    print("\n== libraryfolders.vdf parsing ==")

    sample = (
        '"libraryfolders"\n'
        "{\n"
        '\t"0"\n'
        "\t{\n"
        '\t\t"path"\t\t"D:\\\\\\\\Program Files (x86)\\\\\\\\Steam"\n'
        '\t\t"label"\t\t""\n'
        "\t}\n"
        '\t"1"\n'
        "\t{\n"
        '\t\t"path"\t\t"E:\\\\\\\\SteamLibrary"\n'
        '\t\t"label"\t\t""\n'
        "\t}\n"
        "}\n"
    )

    with tempfile.TemporaryDirectory() as tmp:
        vdf = Path(tmp) / "libraryfolders.vdf"
        vdf.write_text(sample, encoding="utf-8")
        libs = ljcompile._parse_library_folders(str(vdf))

    check("both libraries are parsed", len(libs) == 2, f"got {libs}")
    check("escaped backslashes are unescaped",
          any("Program Files" in x for x in libs), f"got {libs}")
    check("a library on another drive is included",
          any(x.lower().startswith("e:") for x in libs), f"got {libs}")

    # A missing or unreadable file must not raise - it is a normal case on a
    # machine without Steam.
    missing = ljcompile._parse_library_folders(
        str(Path(tempfile.gettempdir()) / "definitely-not-here.vdf"))
    check("a missing vdf yields no libraries instead of raising",
          missing == [], f"got {missing}")


def test_discovery_finds_this_machine() -> None:
    """On a machine with the game, discovery must succeed without hints."""
    print("\n== discovery on this machine ==")

    roots = ljcompile._steam_roots()
    check("Steam roots are discovered from real metadata",
          len(roots) > 0, f"got {roots}")

    # No HD2_LUA_DLL, no explicit path: this is what a user's exe does.
    saved = os.environ.pop("HD2_LUA_DLL", None)
    try:
        found = None
        error = None
        try:
            found = ljcompile.find_lua_dll()
        except Exception as exc:  # noqa: BLE001
            # Catch broadly on purpose: the pre-fix code raised a plain
            # FileNotFoundError here, and a test that dies with a traceback
            # reports nothing useful about which property broke. Any failure is
            # a failure of discovery.
            error = exc

        if found is None:
            # The game may legitimately be absent on a CI machine; the
            # discovery mechanism is still asserted above. But on a machine
            # WITH the game this must not happen.
            check("the game is found without any environment hint", False,
                  f"{type(error).__name__}: {error}")
        else:
            check("the game is found without any environment hint", True)
            check("the found path is a real file", os.path.isfile(found))
            check("the path ends in lua51.dll",
                  found.lower().endswith("lua51.dll"), f"got {found}")
            check("it is inside a Helldivers 2 install",
                  "helldivers 2" in found.lower(), f"got {found}")
            # The old implementation only knew about the Steam directory on
            # C:. A machine whose game lives elsewhere is exactly the case that
            # shipped broken, so assert the search reached beyond a fixed list.
            check("discovery consulted more than one location",
                  len(ljcompile._candidate_dll_paths()) > 3,
                  f"only {len(ljcompile._candidate_dll_paths())} candidates")
    finally:
        if saved is not None:
            os.environ["HD2_LUA_DLL"] = saved


def test_explicit_path_wins() -> None:
    """An explicit path (or HD2_LUA_DLL) must still override discovery."""
    print("\n== explicit override ==")

    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "lua51.dll"
        fake.write_bytes(b"not really a dll, but it exists")

        found = ljcompile.find_lua_dll(str(fake))
        check("an explicit path is used as given",
              os.path.samefile(found, fake), f"got {found}")

        # A bad explicit path must fall through to discovery, not fail outright.
        missing = str(Path(tmp) / "nope.dll")
        try:
            other = ljcompile.find_lua_dll(missing)
        except Exception as exc:  # noqa: BLE001
            # Pre-fix code raised FileNotFoundError from here rather than
            # falling through; report it as a failed check, not a traceback.
            check("a bad explicit path falls back to discovery", False,
                  f"{type(exc).__name__}: {exc}")
        else:
            check("a bad explicit path falls back to discovery",
                  other != missing, f"got {other}")


def test_config_file_escape_hatch() -> None:
    """A user with a non-Steam install needs a way out that is not a CLI flag."""
    print("\n== config file ==")

    with tempfile.TemporaryDirectory() as tmp:
        fake_dll = Path(tmp) / "lua51.dll"
        fake_dll.write_bytes(b"stand-in for the real runtime")

        cfg = Path(tmp) / "cfg.json"
        cfg.write_text(json.dumps({"lua51_dll": str(fake_dll)}), encoding="utf-8")

        saved_path = ljcompile.config_path
        saved_env = os.environ.pop("HD2_LUA_DLL", None)
        try:
            ljcompile.config_path = lambda: cfg  # type: ignore[assignment]
            try:
                found = ljcompile.find_lua_dll()
            except Exception as exc:  # noqa: BLE001
                check("a path saved in the config file is used", False,
                      f"{type(exc).__name__}: {exc}")
            else:
                check("a path saved in the config file is used",
                      os.path.samefile(found, fake_dll), f"got {found}")

            # A malformed config must not break discovery.
            cfg.write_text("{ this is not json", encoding="utf-8")
            check("a malformed config is ignored rather than fatal",
                  ljcompile.load_config() == {}, "load_config did not recover")

            cfg.write_text(json.dumps({"lua51_dll": 12345}), encoding="utf-8")
            check("a non-string path in the config is ignored",
                  ljcompile._config_lua_dll() is None,
                  f"got {ljcompile._config_lua_dll()!r}")

            # A config pointing at a file that no longer exists must fall
            # through to discovery, not fail.
            cfg.write_text(json.dumps({"lua51_dll": str(Path(tmp) / "gone.dll")}),
                           encoding="utf-8")
            try:
                ljcompile.find_lua_dll()
            except Exception as exc:  # noqa: BLE001
                # Acceptable ONLY if discovery also found nothing, which is
                # what the exception says. A traceback here would hide whether
                # the config fallback worked.
                check("a stale config path falls through to discovery",
                      "gone.dll" not in str(exc),
                      f"{type(exc).__name__}: {exc}")
            else:
                check("a stale config path falls through to discovery", True)
        finally:
            ljcompile.config_path = saved_path  # type: ignore[assignment]
            if saved_env is not None:
                os.environ["HD2_LUA_DLL"] = saved_env


def test_discovery_beyond_hardcoded_paths() -> None:
    """A game in a non-default library must be found. This is the shipped bug.

    The user who reported it had the game on a library the tool never looked
    at, and the failure was invisible here: this machine happens to have the
    game at one of the old hardcoded paths (the developer's own D: drive), so
    any test that only asks "does it find lua51.dll on this box?" passes
    against the broken code too.

    So the assertion is not "it finds a dll" - it is "it consults Steam's own
    records", which is the property the fix added and the one a fixed list of
    paths cannot have.
    """
    print("\n== discovery beyond a hardcoded list ==")

    with tempfile.TemporaryDirectory() as tmp:
        # A library on a drive the old list never mentioned, laid out the way
        # Steam lays one out.
        library = Path(tmp) / "SomeOtherDrive" / "SteamLibrary"
        (library / "steamapps" / "common").mkdir(parents=True)
        game_bin = library / "steamapps" / "common" / "Helldivers 2" / "bin"
        game_bin.mkdir(parents=True)
        dll = game_bin / "lua51.dll"
        dll.write_bytes(b"stand-in")

        # Steam's library list points at it, as it would in reality.
        steam_dir = Path(tmp) / "Steam"
        (steam_dir / "steamapps").mkdir(parents=True)
        vdf = steam_dir / "steamapps" / "libraryfolders.vdf"
        escaped = str(library).replace("\\", "\\\\")
        vdf.write_text(
            '"libraryfolders"\n{\n\t"0"\n\t{\n'
            f'\t\t"path"\t\t"{escaped}"\n'
            "\t}\n}\n",
            encoding="utf-8")

        saved_roots = ljcompile._steam_roots
        saved_fallbacks = ljcompile.DEFAULT_DLL_CANDIDATES
        saved_env = os.environ.pop("HD2_LUA_DLL", None)
        saved_cfg = ljcompile.config_path
        try:
            # The registry is not available to the test, so supply the Steam
            # dir directly - the point under test is that the LIBRARY LIST is
            # read, not how the Steam dir itself was found.
            ljcompile._steam_roots = lambda: [  # type: ignore[assignment]
                str(library), str(steam_dir)]
            # Remove the fallback guesses entirely: on this machine one of them
            # exists, and it would mask a failure to use the library list.
            ljcompile.DEFAULT_DLL_CANDIDATES = []  # type: ignore[assignment]
            ljcompile.config_path = lambda: Path(tmp) / "no-such-config.json"  # type: ignore[assignment]

            try:
                found = ljcompile.find_lua_dll()
            except Exception as exc:  # noqa: BLE001
                # Catch broadly: the pre-fix code raises a plain
                # FileNotFoundError here. A test that dies with a traceback
                # still goes red, but reports nothing about which property
                # broke - and this is THE property the fix added.
                found = None
                reason = f"{type(exc).__name__}: {exc}"
            else:
                reason = ""

            check("a game in a non-default library is found",
                  found is not None and os.path.samefile(found, dll),
                  reason or f"got {found}")

            # And the same path is reachable through the vdf parse alone.
            libs = ljcompile._parse_library_folders(str(vdf))
            check("the library is read from Steam's own file",
                  any(os.path.samefile(x, library) for x in libs),
                  f"got {libs}")
        finally:
            ljcompile._steam_roots = saved_roots  # type: ignore[assignment]
            ljcompile.DEFAULT_DLL_CANDIDATES = saved_fallbacks  # type: ignore[assignment]
            ljcompile.config_path = saved_cfg  # type: ignore[assignment]
            if saved_env is not None:
                os.environ["HD2_LUA_DLL"] = saved_env


def test_failure_message_is_actionable() -> None:
    """The error has to tell a GUI user what happened and what to do."""
    print("\n== failure message ==")

    err = ljcompile.LuaDllNotFound([r"C:\some\where\lua51.dll",
                                    r"E:\other\place\lua51.dll"])
    text = str(err)

    check("it says what could not be found",
          "lua51.dll" in text, f"got {text[:120]!r}")
    check("it is written in the user's language (Chinese)",
          any("\u4e00" <= ch <= "\u9fff" for ch in text),
          "no CJK characters in the message")
    check("it lists the paths that were searched",
          r"C:\some\where\lua51.dll" in text, f"got {text[:200]!r}")
    check("it does NOT tell a GUI user to pass --dll",
          "--dll" not in text,
          "the old message told exe users to use a flag they do not have")
    check("it carries the searched list for the UI",
          len(err.searched) == 2, f"got {err.searched}")


def main() -> int:
    print("test_lua_dll_discovery: find the game wherever Steam put it")
    test_vdf_parsing()
    test_discovery_finds_this_machine()
    test_explicit_path_wins()
    test_config_file_escape_hatch()
    test_discovery_beyond_hardcoded_paths()
    test_failure_message_is_actionable()
    print(f"\n{checks - failures}/{checks} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
