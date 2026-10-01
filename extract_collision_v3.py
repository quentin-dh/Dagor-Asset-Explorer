"""
Dagor Engine V3 Collision Resource Extractor (0xACE50003)

Implements the recursive wire-tree parser based on the actual Dagor Engine source:
  - bvhIO.cpp: serializeQuadBLAS / deserializeQuadBLAS / deserializeQuadBLASToSoA4
  - dag_bvhIO.h: BlasIoHeader, wire format documentation
  - dag_swBLAS_leaf.h: QuadLeafFields, decodeQuadLeafFields, expandQuadLeafTris
  - swBLASLeafDefs.hlsli: bit field constants

Wire format (from bvhIO.cpp writeSubtree):
  Each element in the tree stream starts with a 1-byte MARKER:
    0x00 (FULL_LEAF)       -> 16 bytes body: W0(4) + W1(4) + W2(4) + W3(4)
    0xFE (SHORT_LEAF)      -> 8 bytes body: W0(4) + W1(4), W2=0, W3=0
    0xFD (SHORT_LEAF_FLIP) -> 8 bytes body: W0(4) + W1(4), W2=QUAD_FLIPA_FLAG, W3=0
    0xFF (RAW_LEAF)        -> 16 bytes body: verbatim W0+W1+W2+W3 (degenerate no-hit)
    1..252                 -> internal node with N children, followed by N recursive subtrees

  CRITICAL: W1 on the wire stores vertex INDEX (not byte offset) in bits [0:23].
  The serializer converts byte_offset -> vertex_index, the deserializer converts back.

  Verts are written BEFORE the tree on the wire.
  The tree is a forest of top-level subtrees (root node is suppressed).
"""
import struct
import zstandard
import os
import sys
from io import BytesIO
from math import sqrt

sys.setrecursionlimit(10000)

# ---- All the same helpers as before ----
def parse_grp(path):
    with open(path,"rb") as f: raw=f.read()
    raw=raw[0xC:]; OFS=0xC; p=0
    ds=struct.unpack_from('<I',raw,p)[0]+0x10; p+=4
    nmo=struct.unpack_from('<I',raw,p)[0]-OFS; p+=4
    nmn=struct.unpack_from('<I',raw,p)[0]; p+=4; p+=8
    struct.unpack_from('<I',raw,p)[0]; p+=4
    ren=struct.unpack_from('<I',raw,p)[0]; p+=4; p+=8; p+=4; p+=4; p+=8
    nd=raw[p:p+nmo-p]; ns=p+len(nd)
    prev=struct.unpack_from('<I',raw,ns)[0]-0x40
    nm=[]
    for i in range(nmn):
        nxt=-1 if i+1>=nmn else struct.unpack_from('<I',raw,ns+(i+1)*4)[0]-0x40
        nm.append(nd[prev:nxt].decode('utf-8').rstrip('\x00')); prev=nxt
    ro=ns+nmn*4
    entries=[]
    for i in range(ren):
        o=ro+i*12
        entries.append({"name":nm[i],"classId":struct.unpack_from('<I',raw,o)[0],"offset":struct.unpack_from('<I',raw,o+4)[0]})
    for i in range(len(entries)):
        entries[i]["size"]=(entries[i+1]["offset"] if i+1<len(entries) else ds)-entries[i]["offset"]
    return entries

def matmul(A,B):
    R=[[0]*len(B[0]) for _ in range(len(A))]
    for i in range(len(A)):
        for j in range(len(B[0])):
            for k in range(len(B)): R[i][j]+=A[i][k]*B[k][j]
    return R

def xform_dagor(t, v):
    """Apply Dagor TMatrix (12 floats, column-major) to a vertex.
    
    Dagor TMatrix layout in memory (12 floats):
      t[0..2]  = col0 (X axis direction)
      t[3..5]  = col1 (Y axis direction)
      t[6..8]  = col2 (Z axis direction)
      t[9..11] = col3 (translation)
    
    Transform: result = M * v + T
    """
    x = t[0]*v[0] + t[3]*v[1] + t[6]*v[2] + t[9]
    y = t[1]*v[0] + t[4]*v[1] + t[7]*v[2] + t[10]
    z = t[2]*v[0] + t[5]*v[1] + t[8]*v[2] + t[11]
    return (x, y, z)

def unpack_vert21(data,offset):
    v=struct.unpack_from('<Q',data,offset)[0]
    return((v&0x1FFFFF)/32.0,((v>>21)&0x1FFFFF)/32.0,((v>>42)&0x1FFFFF)/32.0)

def sign_extend_13(val):
    val&=0x1FFF; return val-0x2000 if val&0x1000 else val

def decode_leaf_faces_wire(w0, w1, w2, w3, vert_count):
    """Decode leaf W0/W1/W2/W3 from wire format into triangle faces.
    
    WIRE FORMAT: W1 bits [0:23] = vertex INDEX (not byte offset).
    This differs from the runtime format where W1 stores a byte offset >> 2.
    
    Based on decodeQuadLeafFields + expandQuadLeafTris from dag_swBLAS_leaf.h
    """
    # On the wire, W1 low 24 bits = vertex index directly
    base = w1 & 0xFFFFFF
    
    o1 = sign_extend_13(w0)
    o2 = sign_extend_13(w0 >> 13)
    o3_lo5 = (w0 >> 26) & 0x1F
    o3_hi8 = (w1 >> 24) & 0xFF
    o3 = sign_extend_13(o3_lo5 | (o3_hi8 << 5))
    delta_b = w2 & 0xFFFF
    o1b = sign_extend_13(w2 >> 16)
    o2b = sign_extend_13(w3)
    o3b = sign_extend_13(w3 >> 13)
    flip_a = bool(w2 & (1 << 29))
    flip_b = bool(w2 & (1 << 30))
    
    is_single = (o3 == o2)
    has_b = (o1b != o2b)
    is_single_b = (o3b == o2b)
    
    faces = []
    def ok(idx): return 0 <= idx < vert_count
    
    # Validate all vertex indices (matches validateQuadLeafVertexIndices)
    all_ok = ok(base) and ok(base + o1) and ok(base + o2)
    if not is_single:
        all_ok = all_ok and ok(base + o3)
    if has_b:
        bb = base + delta_b
        all_ok = all_ok and ok(bb) and ok(bb + o1b) and ok(bb + o2b)
        if not is_single_b:
            all_ok = all_ok and ok(bb + o3b)
    
    if not all_ok:
        return faces
    
    # Quad A tri 1: always present (expandQuadLeafTris)
    faces.append((base, base + o1, base + o2))
    # Quad A tri 2: present if not single
    if not is_single:
        if flip_a:
            faces.append((base + o2, base + o1, base + o3))
        else:
            faces.append((base + o1, base + o2, base + o3))
    
    # Quad B: present if has_b
    if has_b:
        bb = base + delta_b
        faces.append((bb, bb + o1b, bb + o2b))
        if not is_single_b:
            if flip_b:
                faces.append((bb + o2b, bb + o1b, bb + o3b))
            else:
                faces.append((bb + o1b, bb + o2b, bb + o3b))
    
    return faces

# ---- Wire format constants (from bvhIO.cpp) ----
QUAD_LEAF_FLAG = 1 << 31
QUAD_FLIPA_FLAG = 1 << 29
RAW_LEAF_MARKER = 0xFF
SHORT_LEAF_MARKER = 0xFE
SHORT_LEAF_FLIP_MARKER = 0xFD
MAX_CHILD_COUNT = SHORT_LEAF_FLIP_MARKER - 1  # 252
MAX_TREE_DEPTH = 256

def is_leaf_marker(cc):
    """From bvhIoIsLeafMarker in bvhIO.cpp"""
    return cc == 0 or cc == RAW_LEAF_MARKER or cc == SHORT_LEAF_MARKER or cc == SHORT_LEAF_FLIP_MARKER

def parse_wire_tree_recursive(data, vert_count, stackless_tree_bytes):
    """Parse the wire-format BVH tree using recursive descent.
    
    This implements the exact same logic as Rebuilder::node() and 
    Soa4Deserializer::parseNode() from bvhIO.cpp.
    
    Wire format: each element starts with a 1-byte marker:
      0x00       -> full leaf: 16 bytes body (W0, W1, W2, W3)
      0xFE       -> short leaf (no flip): 8 bytes body (W0, W1); W2=0, W3=0
      0xFD       -> short leaf (flip): 8 bytes body (W0, W1); W2=FLIPA, W3=0
      0xFF       -> raw/degenerate leaf: 16 bytes body (verbatim, no geometry)
      1..252     -> internal node with N children
    
    The tree is a forest of top-level subtrees (the root is suppressed).
    We track a virtual stackless offset (28 bytes per leaf, 16 per internal node)
    and stop when it reaches stackless_tree_bytes (from BlasIoHeader.treeBytes).
    
    Returns list of (v0, v1, v2) face tuples with vertex indices.
    """
    faces = []
    ofs = 0       # wire byte offset
    virt_ofs = 0  # virtual stackless tree offset (for forest stop condition)
    leaf_count = 0
    STACKLESS_LEAF = 28  # BVH_BLAS_LEAF_SIZE: each leaf = 28 stackless bytes
    STACKLESS_NODE = 16  # BVH_BLAS_NODE_SIZE: each internal = 16 stackless bytes
    
    def read_leaf(cc):
        nonlocal ofs, virt_ofs, leaf_count
        virt_ofs += STACKLESS_LEAF
        
        if cc == SHORT_LEAF_MARKER or cc == SHORT_LEAF_FLIP_MARKER:
            if ofs + 8 > len(data):
                raise ValueError(f"Short leaf overruns at wire ofs {ofs}")
            w0, w1 = struct.unpack_from('<II', data, ofs)
            ofs += 8
            w2 = QUAD_FLIPA_FLAG if cc == SHORT_LEAF_FLIP_MARKER else 0
            w3 = 0
        else:  # 0x00 full or 0xFF raw
            if ofs + 16 > len(data):
                raise ValueError(f"Full/raw leaf overruns at wire ofs {ofs}")
            w0, w1, w2, w3 = struct.unpack_from('<IIII', data, ofs)
            ofs += 16
        
        if not (w0 & QUAD_LEAF_FLAG):
            raise ValueError(f"Leaf W0 missing QUAD_LEAF_FLAG at wire ofs {ofs}")
        
        leaf_count += 1
        
        if cc != RAW_LEAF_MARKER:
            leaf_faces = decode_leaf_faces_wire(w0, w1, w2, w3, vert_count)
            faces.extend(leaf_faces)
    
    def read_node_recursive(depth):
        nonlocal ofs, virt_ofs
        if depth > MAX_TREE_DEPTH:
            raise ValueError(f"Tree depth exceeds {MAX_TREE_DEPTH}")
        if ofs >= len(data):
            raise ValueError(f"Unexpected end at wire ofs {ofs}")
        
        cc = data[ofs]
        ofs += 1
        
        if is_leaf_marker(cc):
            read_leaf(cc)
        else:
            # Internal node
            virt_ofs += STACKLESS_NODE
            child_count = cc
            for _ in range(child_count):
                read_node_recursive(depth + 1)
    
    # Forest loop: read top-level subtrees until virtual offset reaches stackless_tree_bytes
    # (same as Rebuilder::node loop and Soa4Deserializer::parseAndSize)
    while virt_ofs < stackless_tree_bytes:
        if ofs >= len(data):
            break
        read_node_recursive(0)
    
    if virt_ofs != stackless_tree_bytes:
        # Warn but don't fail - might be off by edge flags section
        pass
    
    return faces, leaf_count, ofs

def compute_normal(v0,v1,v2):
    e1=(v1[0]-v0[0],v1[1]-v0[1],v1[2]-v0[2]); e2=(v2[0]-v0[0],v2[1]-v0[1],v2[2]-v0[2])
    return(e1[1]*e2[2]-e1[2]*e2[1],e1[2]*e2[0]-e1[0]*e2[2],e1[0]*e2[1]-e1[1]*e2[0])
def normalize(n):
    l=sqrt(n[0]*n[0]+n[1]*n[1]+n[2]*n[2])
    return(n[0]/l,n[1]/l,n[2]/l) if l>0 else(0,0,0)

# ---- Main parser ----
def parse_v3_collision(raw_data):
    magic = struct.unpack_from('<I', raw_data, 0)[0]
    cSz = int.from_bytes(raw_data[4:7], 'little')
    cMethod = raw_data[7]
    
    # Decompress based on compression method
    if cMethod == 0x40:
        d = zstandard.ZstdDecompressor().decompress(raw_data[8:8+cSz])
    elif cMethod == 0x60:
        import zlib
        d = zlib.decompress(raw_data[8:8+cSz])
    else:
        d = zstandard.ZstdDecompressor().decompress(raw_data[8:8+cSz])
    
    counts = struct.unpack_from('<11I', d, 64)
    N = counts[0]
    nr_start = 108 + N * 8
    node_recs = []
    for i in range(N):
        o = nr_start + i * 48
        node_recs.append({"name_ofs": struct.unpack_from('<I',d,o)[0],
                          "idx_count": struct.unpack_from('<I',d,o+4)[0], "type": d[o+19]})
    tm_start = nr_start + N * 48
    tms = [struct.unpack_from('<12f', d, tm_start+i*48) for i in range(counts[1])]
    blas_start = tm_start + counts[1]*48 + counts[2]*48 + counts[3]*48
    
    # IMPORTANT: counts[4] is the IN-MEMORY stackless BLAS size, NOT the wire size.
    # The wire BLAS is much smaller (compact serialization strips boxes/skips).
    # The actual wire BLAS size = stream_size - fixed_sections.
    # Fixed sections after BLAS: PHYS_MAT_POOL + CAPSULES + CONVEX_PLANES + NAMES + MAT_NAMES
    fixed_after_blas = counts[6]*2 + counts[7]*32 + counts[8]*16 + counts[9] + counts[10]
    blas_wire_size = len(d) - blas_start - fixed_after_blas
    blas = d[blas_start:blas_start+blas_wire_size]
    
    # NAMES array starts after BLAS wire data + PHYS_MAT_POOL + CAPSULES + CONVEX_PLANES
    names_ofs = blas_start + blas_wire_size + counts[6]*2 + counts[7]*32 + counts[8]*16
    names_data = d[names_ofs:names_ofs+counts[9]]
    def get_name(off):
        end=names_data.find(b'\x00',off)
        return names_data[off:end if end>=0 else len(names_data)].decode('utf-8',errors='replace')
    
    # Find chunk boundaries via BlasIoHeader magic
    magic_bytes = struct.pack('<I', 0x694C4264)
    mpos = []; s=0
    while True:
        p=blas.find(magic_bytes,s)
        if p<0: break
        mpos.append(p); s=p+1
    frame_starts = [p-36 for p in mpos]
    mesh_indices = [i for i in range(N) if node_recs[i]["idx_count"]>0]
    
    print(f"  {len(d)} bytes decompressed, {N} nodes, {len(mesh_indices)} mesh, {len(mpos)} chunks")
    
    total_expected = 0
    total_decoded = 0
    nodes = []
    for ci, mi in enumerate(mesh_indices):
        if ci >= len(frame_starts): break
        fs = frame_starts[ci]
        ce = frame_starts[ci+1] if ci+1<len(frame_starts) else len(blas)
        
        inv_scale = struct.unpack_from('<3f', blas, fs+12)
        bmin = struct.unpack_from('<3f', blas, fs+24)
        bio_ofs = fs + 36
        
        # Read BlasIoHeader (40 bytes)
        bio_magic = struct.unpack_from('<I', blas, bio_ofs)[0]
        version = struct.unpack_from('<H', blas, bio_ofs+4)[0]
        vert_stride = blas[bio_ofs+6]
        leaf_size = blas[bio_ofs+7]
        tree_bytes = struct.unpack_from('<I', blas, bio_ofs+8)[0]  # stackless tree size (for reference)
        vert_count = struct.unpack_from('<I', blas, bio_ofs+12)[0]
        
        hdr_size = 40
        # Version >= 2 has an extra uint32 io flags
        io_flags = 0
        if version >= 2:
            io_flags = struct.unpack_from('<I', blas, bio_ofs+40)[0]
            hdr_size += 4
        
        verts_start = bio_ofs + hdr_size
        tree_start = verts_start + vert_count * 8
        
        # The tree data ends at the next chunk's CollResChunkFrame start
        tree_end = ce
        
        # If there are edge flags (version >= 2, io_flags & 1), they follow the tree
        # We don't need them for geometry extraction, but the tree data ends before them
        
        # Decode verts
        verts = []
        for vi in range(vert_count):
            off = verts_start + vi * 8
            if off+8 > len(blas): break
            qx,qy,qz = unpack_vert21(blas, off)
            verts.append((qx*inv_scale[0]+bmin[0], qy*inv_scale[1]+bmin[1], qz*inv_scale[2]+bmin[2]))
        
        # Parse wire tree using recursive descent
        tree_data = blas[tree_start:tree_end]
        expected = node_recs[mi]["idx_count"]//3
        total_expected += expected
        
        try:
            faces, leaf_cnt, wire_consumed = parse_wire_tree_recursive(
                tree_data, vert_count, tree_bytes)
        except (ValueError, struct.error) as e:
            print(f"    [{ci}] WARN: recursive parser failed: {e}")
            faces = []
        
        total_decoded += len(faces)
        
        # Transform: apply the AUTHORED_TM for this node.
        # Dagor TMatrix is column-major: col0=X-axis, col1=Y-axis, col2=Z-axis, col3=translation.
        # For collision nodes, the rotation part typically contains a Y<->Z swap
        # (col1 points along Z, col2 points along Y) which converts from the BLAS
        # quantization space to Dagor's Y-up world space.
        # We negate Y to correct the vertical orientation (Dagor's Y-up convention
        # has the opposite handedness from the OBJ/Blender convention).
        if mi < len(tms):
            t = tms[mi]
            wv = [xform_dagor(t, v) for v in verts]
        else:
            wv = list(verts)
        # Flip Y to correct upside-down orientation
        wv = [(v[0], -v[1], v[2]) for v in wv]
        
        name=get_name(node_recs[mi]["name_ofs"]) if node_recs[mi]["name_ofs"]<len(names_data) else ""
        if not name: name=f"node_{mi:03d}"
        nodes.append({"name":name,"verts":wv,"faces":faces})
        if ci<10 or len(faces) != expected:
            status = "OK" if len(faces) == expected else "MISMATCH"
            print(f"    [{ci}] '{name}': {vert_count}v, {expected} expected, {len(faces)} decoded [{status}]")
    
    tv=sum(len(n["verts"]) for n in nodes); tf=sum(len(n["faces"]) for n in nodes)
    pct = (tf/total_expected*100) if total_expected > 0 else 0
    print(f"  Total: {len(nodes)} nodes, {tv} verts, {tf}/{total_expected} faces ({pct:.1f}%)")
    return nodes

# ---- Export ----
def export_obj(nodes, out, name):
    p=os.path.join(out,f"{name}.obj"); vo=0
    with open(p,'w') as f:
        f.write(f"# {name}\n")
        for n in nodes:
            f.write(f"o {n['name']}\n")
            for v in n["verts"]: f.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
            for face in n["faces"]: f.write(f"f {face[0]+vo+1} {face[1]+vo+1} {face[2]+vo+1}\n")
            vo+=len(n["verts"])
    return p

def export_dmf(nodes, out, name):
    p=os.path.join(out,f"{name}.dmf")
    av=[]; au=[]
    for n in nodes: av.extend(n["verts"]); au.extend([(0,0)]*len(n["verts"]))
    vc=len(av)
    norms=[[0,0,0] for _ in range(vc)]
    vo=0
    for n in nodes:
        for face in n["faces"]:
            i0,i1,i2=face[0]+vo,face[1]+vo,face[2]+vo
            if max(i0,i1,i2)<vc:
                nn=compute_normal(av[i0],av[i1],av[i2])
                for idx in(i0,i1,i2): norms[idx][0]+=nn[0];norms[idx][1]+=nn[1];norms[idx][2]+=nn[2]
        vo+=len(n["verts"])
    for i in range(vc): norms[i]=list(normalize(norms[i]))
    ob=BytesIO(); vo=0
    for n in nodes:
        nb=n["name"].encode(); ob.write(struct.pack('<I',len(nb))); ob.write(nb)
        ob.write(struct.pack('<II',0,len(n["faces"])))
        for f in n["faces"]: ob.write(struct.pack('<III',f[0]+vo,f[1]+vo,f[2]+vo))
        mat=f"{name}_{n['name']}".encode()
        ob.write(struct.pack('<I',1)); ob.write(struct.pack('<I',0))
        ob.write(struct.pack('<I',len(mat))); ob.write(mat)
        vo+=len(n["verts"])
    buf=BytesIO()
    buf.write(b"DMF\x02"); buf.write(struct.pack('<III',0x10,0,0))
    buf.write(struct.pack('<fff',1,1,1)); buf.write(struct.pack('<I',vc))
    for v in av: buf.write(struct.pack('<fff',*v))
    for n in norms: buf.write(struct.pack('<fff',*n))
    for u in au: buf.write(struct.pack('<ff',*u))
    buf.write(struct.pack('<I',len(nodes))); buf.write(ob.getvalue())
    with open(p,'wb') as f: f.write(buf.getvalue())
    return p,len(buf.getvalue())

def main():
    if len(sys.argv) < 2:
        print("Usage: extract_collision_v3.py <resource_name> [grp_path]")
        print("  If grp_path is omitted, defaults to uk_aircraft_logic.grp")
        sys.exit(1)
    name = sys.argv[1]
    grp = sys.argv[2] if len(sys.argv) > 2 else r"E:\Dagor Asset Explorer\grp\uk_aircraft_logic.grp"
    out = r"E:\Dagor Asset Explorer\output"
    os.makedirs(out, exist_ok=True)
    entries = parse_grp(grp)
    if name == "--list":
        for e in entries:
            print(f"  {e['name']}  classId=0x{e['classId']:08X}  size={e['size']}")
        sys.exit(0)
    t = next((e for e in entries if e["name"] == name), None)
    if not t:
        print(f"'{name}' not found! Use --list to see available resources.")
        sys.exit(1)
    with open(grp,'rb') as f: f.seek(t["offset"]); ad=f.read(t["size"])
    print(f"Parsing {name} ({len(ad)} bytes)...")
    nodes=parse_v3_collision(ad)
    print(f"\nOBJ: {export_obj(nodes,out,name)}")
    dp,ds=export_dmf(nodes,out,name)
    print(f"DMF: {dp} ({ds} bytes)")

if __name__=="__main__": main()
