import struct
from pathlib import Path

def djb2(name):
    r = 5381
    for ch in name:
        r = (r * 33 + ord(ch)) & 0xFFFFFFFF
    return (r - 5381) & 0xFFFFFFFF

for n in ('DamageSettings','ProjectileSettings','ExplosionSettings','WeaponCustomizationSettings'):
    print('%-30s djb2=0x%08X' % (n, djb2(n)))

print()
for tag,root in (('OLD','data/raw'),('CUR','.tools/current')):
    print('===',tag)
    for f in sorted(Path(root).glob('generated_*.dl_bin')):
        b=f.read_bytes()
        m=b.find(b'LDLD')
        ver,typ,size=struct.unpack_from('<III',b,m+4)
        print('  %-46s magic@%-3d ver=%d type=0x%08X size=%d len=%d' % (f.name,m,ver,typ,size,len(b)))
        # candidate data offsets: 24 and 40
        for off in (24,40):
            o,c=struct.unpack_from('<QQ',b,m+off)
            if 0 < c < 100000 and m+off+o+c*8 <= len(b)+8*100000:
                print('        data_off=%d -> u64[0]=%d u64[1]=%d (as offset: abs=%d)' % (off,o,c,m+off+o))
    # find ALL LDLD instances
    f=sorted(Path(root).glob('generated_damage*.dl_bin'))[0]
    b=f.read_bytes()
    i=b.find(b'LDLD'); n=0
    while i>=0:
        ver,typ,size=struct.unpack_from('<III',b,i+4)
        print('   LDLD @%d ver=%d type=0x%08X size=%d' % (i,ver,typ,size)); n+=1
        i=b.find(b'LDLD',i+1)
    print('   total LDLD in damage file:',n)