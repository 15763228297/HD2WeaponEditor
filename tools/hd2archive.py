"""HD2 (Stingray) patch-archive reader/writer.

Format reverse-engineered from the local install and cross-checked against
CowboyBingus/VanillaPlusMegapack scripts/archive.py. Verified byte-exact by
tests/test_archive.py round-tripping every patch file in the game's data dir.

Layout (little endian):
  0x00  u32 magic = 0xF0000011
  0x04  u32 version = 1
  0x08  u32 resource count
  0x0C  20 bytes zero
  0x20  u64 body_offset (end of last resource, 16-byte aligned) -- == file size
  0x28  u64 zero
  0x30  24 bytes zero
  0x48  u32 zero, u32 zero
  0x50  u64 resource_type (same for every entry in practice)
  0x58  u32 count
  0x5C  u32 zero
  0x60  u32 16
  0x64  u32 16
  0x68  count * 80-byte entries:
          u64 name_hash, u64 type, u64 offset,
          u64 0, u64 0, u64 0, u64 0,
          u32 size, u32 0, u32 0, u32 16, u32 16, u32 index
  then resources, each padded to a 16-byte boundary.

Lua resources carry an 8-byte header: u32 payload_size, u32 version(2),
followed by LuaJIT bytecode starting with ESC 'L' 'J' 02 02 (stripped).

Two writer variants exist in the wild, both accepted by the engine:
  * 16-aligned resource offsets, per-entry/type-block flag 16
    -- what BingusSharedLoader and the megapack ship (our target; see
       patch_17..patch_26 in a live install, which this module reproduces
       byte for byte).
  * unaligned offsets (resource starts right after the entry table), flag 64
    -- what the HD2SDK Blender add-on writes (patch_0, patch_3..patch_9,
       patch_11).
`Archive.variant` reports which one a parsed file uses. We only ever *write*
the first variant, because it is the one proven to load in this install.

Archives with version 2 or 3 (patch_1/2/12..16) use a different layout used
for settings/stream payloads. They carry no Lua resources, we never write
them, and parse() reports them as UnsupportedLayout instead of guessing.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

MAGIC = 0xF0000011
HEADER_SIZE = 104          # fixed header + type block
ENTRY_SIZE = 80
TYPE_LUA = 0xA14E8DFA2CD117E2
LUA_RESOURCE_VERSION = 2
# LuaJIT bytecode starts with ESC 'L' 'J' <version> <flags>; version 2 == 2.1,
# and flags bit 0x02 means the debug info was stripped (what mods ship).
LUA_BC_MAGIC = b"\x1bLJ\x02"          # 4 bytes: ESC L J 02
LUA_BC_STRIP_FLAG = 0x02
FLAG_BINGUS = 16
FLAG_SDK = 64


class UnsupportedLayout(ValueError):
    """Archive uses a layout this module does not model (version 2/3)."""


_MASK64 = (1 << 64) - 1
_MIX = 0xC6A4A7935BD1E995


def resource_hash(name: str) -> int:
    """64-bit Murmur variant used for Stingray resource IDs."""
    data = name.encode("utf-8")
    value = len(data) * _MIX & _MASK64
    end = len(data) // 8 * 8
    for (word,) in struct.iter_unpack("<Q", data[:end]):
        word = word * _MIX & _MASK64
        word ^= word >> 47
        value = (value ^ (word * _MIX & _MASK64)) * _MIX & _MASK64
    if data[end:]:
        value = (value ^ int.from_bytes(data[end:], "little")) * _MIX & _MASK64
    value ^= value >> 47
    value = value * _MIX & _MASK64
    return value ^ (value >> 47)


def _align16(n: int) -> int:
    return (n + 15) & ~15


# Native Windows paths: the Hermes shell is git-bash, which does not translate
# MSYS paths like /d/foo for native Python, so default to the real install.
DATA_DIR_CANDIDATES = [
    r"D:\program files (x86)\steam\steamapps\common\Helldivers 2\data",
    r"C:\Program Files (x86)\Steam\steamapps\common\Helldivers 2\data",
]


def default_data_dir() -> str:
    for candidate in DATA_DIR_CANDIDATES:
        if os.path.isdir(candidate):
            return candidate
    raise SystemExit("Helldivers 2 data dir not found; pass it explicitly")


@dataclass
class Resource:
    name_hash: int
    type: int
    data: bytes
    # Entry words we have not identified: the four u64 at entry+24..55 (zero in
    # every archive seen) and the two u64 at entry+40/+48 (0xb000 / 0x7c100 in
    # SDK-written archives, zero in Bingus-written ones). Preserved verbatim so
    # round-tripping an existing archive is byte-exact; new archives use zeros.
    extra: tuple[int, ...] = (0, 0, 0, 0)
    # entry word 9, zero in every archive seen except one SDK patch (1928).
    # Preserved verbatim for the same reason.
    marker: int = 0

    @property
    def payload(self) -> bytes:
        """Resource bytes without the 8-byte Lua header, if it has one."""
        if self.type == TYPE_LUA and len(self.data) >= 8:
            size, = struct.unpack_from("<I", self.data, 0)
            if size == len(self.data) - 8 and self.data[8:12] == LUA_BC_MAGIC:
                return self.data[8:]
        return self.data

    def lua_bytecode(self) -> bytes | None:
        payload = self.payload
        return payload if payload[:len(LUA_BC_MAGIC)] == LUA_BC_MAGIC else None


@dataclass
class Archive:
    resources: list[Resource]
    variant: str = field(default="bingus")
    # Header fields whose meaning we have not established (raw20 at 0x0C,
    # the u64 at 0x20, the u64 at 0x28). The Bingus writer leaves them zero
    # except for 0x20 == body offset; the SDK writer puts values there. We
    # never invent values: parsed archives keep their own, new archives get
    # the Bingus defaults that are proven to load in this install.
    raw20: bytes = b"\x00" * 20
    q32: int | None = None
    q40: int = 0

    @classmethod
    def parse(cls, blob: bytes) -> "Archive":
        if len(blob) < HEADER_SIZE:
            raise UnsupportedLayout("file shorter than the archive header")
        magic, version, count = struct.unpack_from("<III", blob, 0)
        if magic != MAGIC:
            raise ValueError(f"not an HD2 archive: magic {magic:#x}")
        if version != 1:
            raise UnsupportedLayout(
                f"archive version {version} uses a layout this module does not model"
            )
        if count == 0 or HEADER_SIZE + count * ENTRY_SIZE > len(blob):
            raise UnsupportedLayout(f"implausible resource count {count}")

        raw20 = blob[0x0C:0x20]
        q32, q40 = struct.unpack_from("<QQ", blob, 0x20)
        first_offset, = struct.unpack_from("<Q", blob, HEADER_SIZE + 16)
        expected_bingus = _align16(HEADER_SIZE + ENTRY_SIZE * count)
        variant = "bingus" if first_offset == expected_bingus else "sdk"

        out: list[Resource] = []
        for i in range(count):
            base = HEADER_SIZE + i * ENTRY_SIZE
            fields = struct.unpack_from("<7Q6I", blob, base)
            name_hash, rtype, offset, size = fields[0], fields[1], fields[2], fields[7]
            if offset + size > len(blob):
                raise ValueError(f"entry {i} runs past end of file")
            extra = (fields[3], fields[4], fields[5], fields[6])
            out.append(Resource(name_hash, rtype, blob[offset:offset + size], extra,
                                fields[9]))
        return cls(out, variant, raw20, q32, q40)

    @classmethod
    def from_file(cls, path) -> "Archive":
        with open(path, "rb") as handle:
            return cls.parse(handle.read())

    def build(self, variant: str | None = None) -> bytes:
        """Serialise.

        `variant` defaults to this archive's own variant, so a parsed file
        round-trips byte for byte in either writer style. New archives default
        to the Bingus style (16-aligned), which is what ships in this install.
        """
        if not self.resources:
            raise ValueError("an archive needs at least one resource")
        variant = variant or self.variant
        if variant not in ("bingus", "sdk"):
            raise ValueError(f"unknown variant {variant!r}")
        align = _align16 if variant == "bingus" else (lambda n: n)
        flag = FLAG_BINGUS if variant == "bingus" else FLAG_SDK
        resource_type = self.resources[0].type

        count = len(self.resources)
        offset = align(HEADER_SIZE + ENTRY_SIZE * count)
        entries = bytearray()
        body = bytearray(offset)
        for index, resource in enumerate(sorted(self.resources, key=lambda r: r.name_hash)):
            extra = list(resource.extra) + [0] * (4 - len(resource.extra))
            entries += struct.pack(
                "<7Q6I",
                resource.name_hash, resource_type, offset, *extra,
                # tail is (size, 0, marker, 16, flag, index). `marker` is 0 in
                # every archive but one SDK patch (1928) and is preserved
                # per-resource; the 16 is constant; `flag` and the index base
                # are what differ between the two writer variants.
                len(resource.data), 0, resource.marker, 16, flag,
                index if variant == "bingus" else index + 1,
            )
            body += resource.data
            if variant == "bingus":
                body += b"\x00" * (-len(body) % 16)
            offset = len(body)
        header = struct.pack(
            "<III20sQQ24s", MAGIC, 1, count, self.raw20,
            self.q32 if self.q32 is not None else offset, self.q40, b"",
        )
        types = struct.pack("<IIQIIII", 0, 0, resource_type, count, 0, 16, flag)
        body[:HEADER_SIZE + len(entries)] = header + types + entries
        return bytes(body)

    def write(self, path) -> None:
        with open(path, "wb") as handle:
            handle.write(self.build())

    def find(self, name: str) -> Resource | None:
        wanted = resource_hash(name)
        for resource in self.resources:
            if resource.name_hash == wanted:
                return resource
        return None

    def names(self) -> list[str]:
        return [f"0x{r.name_hash:016X}" for r in self.resources]


def wrap_lua(bytecode: bytes) -> bytes:
    """Add the 8-byte resource header the engine expects for Lua resources."""
    if bytecode[:len(LUA_BC_MAGIC)] != LUA_BC_MAGIC:
        raise ValueError(f"not LuaJIT bytecode: {bytecode[:4]!r}")
    return struct.pack("<II", len(bytecode), LUA_RESOURCE_VERSION) + bytecode


def _main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list resources in an archive")
    p_list.add_argument("archive")

    p_ext = sub.add_parser("extract", help="extract one resource by name")
    p_ext.add_argument("archive")
    p_ext.add_argument("name")
    p_ext.add_argument("out")

    p_lua = sub.add_parser("extract-lua",
                           help="dump every Lua resource as bytecode + string table")
    p_lua.add_argument("archive")
    p_lua.add_argument("outdir")

    p_scan = sub.add_parser("scan", help="scan a whole data dir for Lua resources")
    p_scan.add_argument("directory", nargs="?", default=None)
    p_scan.add_argument("--grep", help="only report resources containing this text")

    p_pack = sub.add_parser("pack", help="build an archive from NAME=PATH pairs")
    p_pack.add_argument("out")
    p_pack.add_argument("pairs", nargs="+")

    p_hash = sub.add_parser("hash", help="resource hash for a name")
    p_hash.add_argument("names", nargs="+")

    args = parser.parse_args()
    if args.cmd == "list":
        archive = Archive.from_file(args.archive)
        for resource in archive.resources:
            lua = resource.lua_bytecode()
            kind = "lua" if lua else "raw"
            print(f"0x{resource.name_hash:016X} type=0x{resource.type:016X} "
                  f"{len(resource.data):>8} bytes {kind}")
    elif args.cmd == "extract":
        resource = Archive.from_file(args.archive).find(args.name)
        if resource is None:
            print("not found", file=sys.stderr)
            raise SystemExit(1)
        payload = resource.lua_bytecode() or resource.data
        with open(args.out, "wb") as handle:
            handle.write(payload)
        print(f"wrote {len(payload)} bytes to {args.out}")
    elif args.cmd == "extract-lua":
        import glob as _glob
        import re as _re
        os.makedirs(args.outdir, exist_ok=True)
        written = 0
        for path in sorted(_glob.glob(args.archive)):
            archive = Archive.from_file(path)
            stem = os.path.splitext(os.path.basename(path))[0]
            for resource in archive.resources:
                lua = resource.lua_bytecode()
                if not lua:
                    continue
                target = os.path.join(args.outdir, f"{stem}-0x{resource.name_hash:016X}.ljbc")
                with open(target, "wb") as handle:
                    handle.write(lua)
                text = _re.sub(rb"[^\x20-\x7e]", b"\n", lua).decode()
                strings = [s for s in text.split("\n") if len(s) >= 5]
                listing = os.path.join(args.outdir,
                                       f"{stem}-0x{resource.name_hash:016X}.strings.txt")
                with open(listing, "w", encoding="utf-8") as handle:
                    handle.write("\n".join(strings))
                written += 1
                print(f"{os.path.basename(path)} 0x{resource.name_hash:016X} "
                      f"{len(lua)}B -> {os.path.basename(target)} "
                      f"({len(strings)} strings)")
        print(f"{written} lua resources extracted to {args.outdir}")
    elif args.cmd == "scan":
        import glob as _glob
        directory = args.directory or default_data_dir()
        found = 0
        for path in sorted(_glob.glob(os.path.join(directory, "*"))):
            if not os.path.isfile(path) or path.endswith((".stream", ".gpu_resources")):
                continue
            try:
                archive = Archive.from_file(path)
            except (ValueError, OSError):
                continue
            for resource in archive.resources:
                lua = resource.lua_bytecode()
                if not lua:
                    continue
                if args.grep and args.grep.encode() not in lua:
                    continue
                found += 1
                print(f"{os.path.basename(path):32s} 0x{resource.name_hash:016X} "
                      f"{len(lua):>8}B {archive.variant}")
        print(f"{found} lua resources")
    elif args.cmd == "pack":
        resources = []
        for pair in args.pairs:
            name, _, path = pair.partition("=")
            with open(path, "rb") as handle:
                data = handle.read()
            if not name.startswith("0x"):
                data = wrap_lua(data) if data[:4] == LUA_BC_MAGIC else data
                name_hash = resource_hash(name)
            else:
                name_hash = int(name, 16)
            resources.append(Resource(name_hash, TYPE_LUA, data))
        Archive(resources).write(args.out)
        print(f"wrote {args.out}")
    else:
        for name in args.names:
            print(f"0x{resource_hash(name):016X}  {name}")


if __name__ == "__main__":
    _main()
