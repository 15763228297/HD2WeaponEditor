import struct, pathlib
g = pathlib.Path(r"D:\Program Files (x86)\Steam\steamapps\common\Helldivers 2\data\game\game.dll")
print("exists", g.exists())
if not g.exists(): raise SystemExit(0)
print("size", g.stat().st_size)
with g.open("rb") as fh:
    head = fh.read(8 * 1024 * 1024)
print("mz", head[:2])
pe = struct.unpack_from("<I", head, 0x3c)[0]
print("pe_off", hex(pe), "sig", head[pe:pe+4])
nsec = struct.unpack_from("<H", head, pe + 6)[0]
optsz = struct.unpack_from("<H", head, pe + 20)[0]
sec = pe + 24 + optsz
for i in range(nsec):
    o = sec + i * 40
    nm = head[o:o+8].rstrip(b"\x00").decode("latin1")
    vsz, va, rsz, ra = struct.unpack_from("<IIII", head, o + 8)
    print("sec", nm, "va", hex(va), "vsz", vsz, "raw", hex(ra), rsz)
print("LDLD count in head8MB:", head.count(b"LDLD"))
for nm, th in (("damage", 0xE0A72CF0), ("projectile", 0xBD4042C2), ("explosion", 0x2AEA2592)):
    sig = b"LDLD" + struct.pack("<I", 1) + struct.pack("<I", th)
    print("sig", nm, "at", head.find(sig))
