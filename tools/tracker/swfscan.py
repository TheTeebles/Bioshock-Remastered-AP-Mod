import sys, struct, zlib
from collections import Counter
NAMES={0:'End',1:'ShowFrame',2:'DefineShape',4:'PlaceObject',5:'RemoveObject',6:'DefineBits',8:'JPEGTables',9:'SetBackgroundColor',
10:'DefineFont',11:'DefineText',12:'DoAction',13:'DefineFontInfo',14:'DefineSound',20:'DefineBitsLossless',21:'DefineBitsJPEG2',
22:'DefineShape2',26:'PlaceObject2',28:'RemoveObject2',32:'DefineShape3',33:'DefineText2',34:'DefineButton2',35:'DefineBitsJPEG3',
36:'DefineBitsLossless2',37:'DefineEditText',39:'DefineSprite',43:'FrameLabel',48:'DefineFont2',56:'ExportAssets',57:'ImportAssets',
59:'DoInitAction',69:'FileAttributes',70:'PlaceObject3',71:'ImportAssets2',75:'DefineFont3',76:'SymbolClass',77:'Metadata',
82:'DoABC',83:'DefineShape4',84:'DefineMorphShape2',86:'DefineSceneAndFrameLabelData',87:'DefineBinaryData',88:'DefineFontName',90:'DefineBitsJPEG4',91:'DefineFont4'}
def load(path):
    d=open(path,'rb').read()
    sig=d[:3]; ver=d[3]
    body=d[8:]
    if sig==b'CWS': body=zlib.decompress(body)
    nbits=body[0]>>3; rectbytes=(5+4*nbits+7)//8
    p=rectbytes+4
    return ver, body, p
def tags(body,p,end=None):
    end=end or len(body)
    while p<end:
        h=struct.unpack_from('<H',body,p)[0]; p+=2
        code=h>>6; ln=h&63
        if ln==63: ln=struct.unpack_from('<I',body,p)[0]; p+=4
        yield code, p, ln
        p+=ln
        if code==0: break

class Bits:
    def __init__(s,b,p): s.b=b; s.p=p*8
    def u(s,n):
        v=0
        for _ in range(n):
            v=(v<<1)|((s.b[s.p>>3]>>(7-(s.p&7)))&1); s.p+=1
        return v
    def sg(s,n):
        v=s.u(n)
        return v-(1<<n) if n and v>>(n-1) else v
    def align(s): s.p=(s.p+7)//8*8
    def byte(s): return s.p//8
def matrix(b,p):
    bt=Bits(b,p); sx=sy=1.0; r0=r1=0.0
    if bt.u(1): n=bt.u(5); sx=bt.sg(n)/65536; sy=bt.sg(n)/65536
    if bt.u(1): n=bt.u(5); r0=bt.sg(n)/65536; r1=bt.sg(n)/65536
    n=bt.u(5); tx=bt.sg(n)/20; ty=bt.sg(n)/20
    bt.align(); return (sx,r0,r1,sy,tx,ty), bt.byte()
def rect(b,p):
    bt=Bits(b,p); n=bt.u(5); v=[bt.sg(n)/20 for _ in range(4)]; bt.align(); return v, bt.byte()
def place2(b,q):
    fl=b[q]; depth=struct.unpack_from('<H',b,q+1)[0]; r=q+3; out={'depth':depth}
    if fl&2: out['char']=struct.unpack_from('<H',b,r)[0]; r+=2
    if fl&4: out['m'],r=matrix(b,r)
    if fl&8:
        # cxform with alpha
        bt=Bits(b,r); ha=bt.u(1); hm=bt.u(1); n=bt.u(4)
        if hm: [bt.u(n) for _ in range(4)]
        if ha: [bt.u(n) for _ in range(4)]
        bt.align(); r=bt.byte()
    if fl&16: r+=2
    if fl&32: e=b.index(b'\0',r); out['name']=b[r:e].decode('latin1'); r=e+1
    return out
def index(path):
    ver,b,p=load(path); defs={}; ex={}
    for code,q,ln in tags(b,p):
        if code in (2,22,32,83,39,37,34,11,33,46,84):
            cid=struct.unpack_from('<H',b,q)[0]; defs[cid]=(code,q,ln)
        if code==56:
            n=struct.unpack_from('<H',b,q)[0]; r=q+2
            for _ in range(n):
                cid=struct.unpack_from('<H',b,r)[0]; r+=2; e=b.index(b'\0',r); ex.setdefault(b[r:e].decode('latin1'),cid); r=e+1
    return b,defs,ex
def sprite_children(b,defs,cid):
    code,q,ln=defs[cid]; out=[]
    for c,qq,l in tags(b,q+4,q+ln):
        if c==26: out.append(place2(b,qq))
        if c==1 and out: break
    return out
