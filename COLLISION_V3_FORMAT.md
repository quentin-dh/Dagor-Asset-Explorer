# Dagor Engine V3 Collision Resource Format (`0xACE50003`)

## Complete Reverse Engineering Documentation

This document describes the binary format of Dagor Engine's compressed collision
geometry resources (magic `0xACE50003`), as found in War Thunder `.grp` files.
Based on analysis of the open-source DagorEngine code at
https://github.com/GaijinEntertainment/DagorEngine

---

## 1. Overview

The collision resource uses class ID `0xACE50000`. When the low 16 bits are non-zero
(e.g. `0xACE50003` = version 3), the data after the 4-byte magic is compressed.

### Outer container (in GRP file)

```
Offset  Size  Description
0       4     Magic: 0xACE50003 (uint32 LE)
4       3     Compressed data size in bytes (uint24 LE)
7       1     Compression method: 0x40=zstd, 0x60=zlib, 0x20=lzma, 0x80=oodle
8       N     Compressed data (N = compressed size)
```

Decompress the data to get the collision resource stream.

---

## 2. Decompressed Stream Layout

The stream follows this exact order (all little-endian):

```
[CollResStreamHeader]           108 bytes
[CollResBindRec * nodeCount]    8 bytes each
[CollResNodeRec * nodeCount]    48 bytes each  (array NODES)
[TMatrix * tmCount]             48 bytes each  (array AUTHORED_TM)
[TMatrix * itmCount]            48 bytes each  (array AUTHORED_ITM, often 0)
[TMatrix * rgtCount]            48 bytes each  (array REL_GEOM_TMS, often 0)
[NODE_BLAS data]                blasSize bytes  (array NODE_BLAS)
[uint16 * physMatCount]         2 bytes each   (array PHYS_MAT_POOL)
[Capsule * capsuleCount]        32 bytes each  (array CAPSULES)
[plane3f * convexCount]         16 bytes each  (array CONVEX_PLANES)
[char * namesSize]              namesSize bytes (array NAMES, null-terminated strings)
[char * matNamesSize]           matNamesSize bytes (array MAT_NAMES)
```

---

## 3. CollResStreamHeader (108 bytes)

```
Offset  Size  Type       Field
0       16    float[4]   boundingSphere (center xyz + radius^2)
16      16    float[4]   bindTraceSphere (widened sphere)
32      12    float[3]   boundingBox.min (xyz)
44      12    float[3]   boundingBox.max (xyz)
56      4     float      boundingSphereRad
60      4     uint32     collisionFlags
64      44    uint32[11] counts - element counts per array:
  counts[0]  = NODES count (= nodeCount)
  counts[1]  = AUTHORED_TM count (= nodeCount usually)
  counts[2]  = AUTHORED_ITM count (usually 0)
  counts[3]  = REL_GEOM_TMS count (usually 0)
  counts[4]  = NODE_BLAS total byte size **IN MEMORY (stackless), NOT wire size**
  counts[5]  = TLAS count (always 0 on wire, rebuilt at load)
  counts[6]  = PHYS_MAT_POOL count
  counts[7]  = CAPSULES count
  counts[8]  = CONVEX_PLANES count
  counts[9]  = NAMES byte size
  counts[10] = MAT_NAMES byte size
```

**IMPORTANT**: `counts[4]` is the in-memory stackless BLAS size, which is LARGER
than the wire BLAS data in the stream. The wire BLAS uses compact serialization
(no boxes, no skip words, short leaves) and is significantly smaller. To find the
actual wire BLAS size, compute:
```
wire_blas_size = stream_size - fixed_before_blas - fixed_after_blas
fixed_after_blas = counts[6]*2 + counts[7]*32 + counts[8]*16 + counts[9] + counts[10]
```

Source: `prog/engine/sharedInclude/gameRes/collResStream.h`

---

## 4. CollResBindRec (8 bytes per node)

```
Offset  Size  Type     Field
0       4     float    maxTmScale
4       1     uint8    flags (transform class: IDENT/TRANSLATE/ORTHONORMALIZED/ORTHOUNIFORM)
5       1     uint8    status (TRACEABLE|GEOMETRY_BAKED|RETAINED_BAKE|COMPOSABLE)
6       2     uint16   behaviorFlags
```

---

## 5. CollResNodeRec (48 bytes per node)

```
Offset  Size  Type     Field
0       4     uint32   nameOfs (byte offset into NAMES array; 0 = empty string)
4       4     uint32   indicesCount (face_count * 3; 0 = no geometry)
8       2     uint16   behaviorFlags
10      2     int16    physMatId (>=0: material name index; -1: none; <=-2: pool slice)
12      2     uint16   insideOfNode (0xFFFF = not contained, else parent node index)
14      2     uint16   capsuleOrPlanesOfs
16      2     uint16   planesCount
18      1     uint8    flags (class bits + TRACE_TWO_SIDED)
19      1     uint8    type (CollisionResourceNodeType enum):
                         0 = MESH
                         1 = POINTS
                         2 = BOX
                         3 = SPHERE
                         4 = CAPSULE
                         5 = CONVEX
20      24    float[6] modelBBox (min xyz, max xyz)
44      4     float    radiusAroundBoxCenter
```

**Key**: Nodes with `indicesCount > 0` have geometry in NODE_BLAS.
Total faces = sum(indicesCount) / 3 across all mesh nodes.

Source: `prog/dagorInclude/gameRes/dag_collisionResource.h`

---

## 6. TMatrix (48 bytes, column-major)

```
float[12] stored as:
  t[0..2]  = col0 (X axis direction in world space)
  t[3..5]  = col1 (Y axis direction in world space)
  t[6..8]  = col2 (Z axis direction in world space)
  t[9..11] = col3 (translation / position in world space)
```

Dagor TMatrix is column-major. The transform to apply is:
```python
def xform_dagor(t, v):
    """result = M * v + T"""
    x = t[0]*v[0] + t[3]*v[1] + t[6]*v[2] + t[9]
    y = t[1]*v[0] + t[4]*v[1] + t[7]*v[2] + t[10]
    z = t[2]*v[0] + t[5]*v[1] + t[8]*v[2] + t[11]
    return (x, y, z)
```

For collision nodes, the rotation part is typically a Y<->Z permutation:
`col0=(1,0,0), col1=(0,0,1), col2=(0,1,0)` - this converts from the BLAS
quantization space (where Y and Z are swapped) to Dagor's Y-up world space.
The translation is the node's position in world coordinates.

**No additional axis swaps are needed for OBJ export** - Dagor uses Y-up,
same as OBJ. When importing into Blender, use the standard Z-up import
option (Blender adds Rotation X=90 automatically).

---

## 7. NODE_BLAS Data (Per-Node Geometry Chunks)

The NODE_BLAS byte array contains geometry chunks concatenated for each mesh node
(nodes with `indicesCount > 0`), in node order.

### Per-chunk layout on wire:

```
[CollResChunkFrame]    36 bytes
[BlasIoHeader]         40 bytes
[uint32 ioFlags]       4 bytes (only if BlasIoHeader.version >= 2)
[vert21 data]          vertCount * 8 bytes
[tree data]            variable (compact wire format, NOT stackless)
[edge flags]           variable (optional, if ioFlags & 1)
```

### 7.1 CollResChunkFrame (36 bytes)

```
Offset  Size  Type      Field
0       12    float[3]  scale (quantization scale: node-local -> q-space)
12      12    float[3]  invScale (decode scale: q-space -> node-local)
24      12    float[3]  bmin (quantization origin = node bbox min)
```

Source: `prog/engine/sharedInclude/gameRes/collResStream.h`

### 7.2 BlasIoHeader (40 bytes)

```
Offset  Size  Type      Field
0       4     int32     magic = 0x694C4264 ('dBLi' in ASCII)
4       2     uint16    version (1 or 2; current = 2)
6       1     uint8     vertStride (always 8 for vert21)
7       1     uint8     leafSize (= BVH_BLAS_LEAF_SIZE = 28)
8       4     uint32    treeBytes (size of RECONSTRUCTED stackless tree, NOT wire tree)
12      4     uint32    vertCount (number of vert21 vertices)
16      12    float[3]  bmin (whole-BLAS box min)
28      12    float[3]  bmax (whole-BLAS box max)
```

**IMPORTANT**: `treeBytes` is the size of the reconstructed in-memory stackless tree,
NOT the size of the wire tree data. To find the actual wire tree size, use chunk
boundaries (distance between successive BlasIoHeader positions).

Source: `prog/dagorInclude/daBVH/dag_bvhIO.h`

### 7.3 Finding Chunk Boundaries

Since `treeBytes` doesn't give wire tree size, locate chunks by scanning for the
BlasIoHeader magic (`0x694C4264`) at any byte alignment within the NODE_BLAS data.
Each magic is at `chunk_start + 36` (after the CollResChunkFrame).

```python
magic_bytes = struct.pack('<I', 0x694C4264)
positions = []
s = 0
while True:
    p = blas_data.find(magic_bytes, s)
    if p < 0: break
    positions.append(p)
    s = p + 1
# frame_starts[i] = positions[i] - 36
# chunk_end[i] = frame_starts[i+1] (or end of blas for last chunk)
```

The number of magic positions should equal the number of mesh nodes.

---

## 8. Vert21 Format (8 bytes per vertex)

Each vertex is a `uint64` (little-endian) packing three 21-bit unsigned integers:

```
Bits [0..20]  = X (21 bits, unsigned, stored as round(value * 32))
Bits [21..41] = Y (21 bits)
Bits [42..62] = Z (21 bits)
Bit  63       = unused
```

### Decoding:

```python
def unpack_vert21(data, offset):
    v = struct.unpack_from('<Q', data, offset)[0]
    x = (v & 0x1FFFFF) / 32.0
    y = ((v >> 21) & 0x1FFFFF) / 32.0
    z = ((v >> 42) & 0x1FFFFF) / 32.0
    return (x, y, z)
```

### Dequantization to node-local space:

```python
local_x = qx * invScale[0] + bmin[0]
local_y = qy * invScale[1] + bmin[1]
local_z = qz * invScale[2] + bmin[2]
```

Where `invScale` and `bmin` are from the CollResChunkFrame.

Source: `prog/dagorInclude/daBVH/dag_swBLAS_leaf.h`, function `unpackVert21`

---

## 9. Face/Triangle Data (Wire BVH Tree)

### 9.1 Overview

Faces are NOT stored as a simple index buffer. They are embedded in a compact
BVH tree structure. The wire format strips bounding boxes and skip words,
keeping only:
- Per internal node: child count (1 byte)
- Per leaf: topology words (W0, W1, W2, W3 = 16 bytes)

Source: `prog/dagorInclude/daBVH/dag_bvhIO.h` (banner comment)

### 9.2 Leaf Format (Double-Quad, 4 uint32 words)

Each leaf encodes 1-4 triangles as two "quads" (A and B), each with up to 2 triangles.

**W0** (also the "skip word" in stackless format, bit 31 always SET for leaves):

```
Bits [0..12]:   o1A  (13 bits, SIGNED - vertex offset from base)
Bits [13..25]:  o2A  (13 bits, SIGNED)
Bits [26..30]:  o3A_low (5 bits, low part of o3A)
Bit  31:        QUAD_LEAF_FLAG (always 1)
```

**W1**:

```
Bits [0..23]:   baseA (24 bits, UNSIGNED - apex byte offset >> 2)
Bits [24..31]:  o3A_high (8 bits)
                Full o3A = sign_extend_13(o3A_low | (o3A_high << 5))
```

**W2**:

```
Bits [0..15]:   deltaB (16 bits, UNSIGNED - quad B base offset in vertex units)
Bits [16..28]:  o1B (13 bits, SIGNED)
Bit  29:        flipA (quad A 2nd triangle winding flip)
Bit  30:        flipB (quad B 2nd triangle winding flip)
Bit  31:        spare
```

**W3**:

```
Bits [0..12]:   o2B (13 bits, SIGNED)
Bits [13..25]:  o3B (13 bits, SIGNED)
Bits [26..31]:  user bits (6 bits, material palette index)
```

### 9.3 Decoding Faces from a Leaf

```python
def sign_extend_13(val):
    val &= 0x1FFF
    return val - 0x2000 if val & 0x1000 else val

base = ((w1 & 0xFFFFFF) << 2) // 8   # vertex index (base_bytes / vert_stride)

# Quad A triangle 1: always present
faces.append((base, base + o1, base + o2))

# Quad A triangle 2: present if o3 != o2 (not "single")
if o3 != o2:
    if flipA: faces.append((base + o2, base + o1, base + o3))
    else:     faces.append((base + o1, base + o2, base + o3))

# Quad B: present if o1b != o2b
if o1b != o2b:
    bb = base + deltaB
    faces.append((bb, bb + o1b, bb + o2b))
    # Quad B triangle 2: present if o3b != o2b
    if o3b != o2b:
        if flipB: faces.append((bb + o2b, bb + o1b, bb + o3b))
        else:     faces.append((bb + o1b, bb + o2b, bb + o3b))
```

Source: `prog/dagorInclude/daBVH/dag_swBLAS_leaf.h`, functions
`decodeQuadLeafFields` and `expandQuadLeafTris`

### 9.4 Sentinel Values

- `o3 == o2` → quad A has only 1 triangle (single)
- `o1b == o2b` → no quad B (leaf has only quad A)
- `o3b == o2b` → quad B has only 1 triangle

---

## 10. Wire Tree Format (Compact Serialization)

### 10.1 What the writer does (from `bvhIO.cpp`)

The writer (`serializeQuadBLAS` + `writeSubtree` in `bvhIO.cpp`) strips bounding
boxes and skip words, writing only tree structure + leaf topology. Each element
in the stream begins with a **1-byte marker**:

```
Marker byte   Meaning                       Body that follows
0x00          Full leaf                     16 bytes: W0 + W1 + W2 + W3
0xFE          Short leaf (flip clear)       8 bytes:  W0 + W1  (W2=0, W3=0 implied)
0xFD          Short leaf (flip set)         8 bytes:  W0 + W1  (W2=QUAD_FLIPA_FLAG, W3=0)
0xFF          RAW/degenerate no-hit leaf    16 bytes: verbatim W0 + W1 + W2 + W3
1..252        Internal node with N children  N recursive subtrees follow
```

**Key wire transformations:**
- **W1 stores vertex INDEX** (not byte offset): bits [0:23] = apex vertex index.
  The serializer converts `(relBaseBytes + leafOfs - vertsOfs) / vertStride` to an index.
  The high 8 bits of W1 (o3A_high) ride along unchanged.
- **Short leaves** halve the body: a single-quad leaf with no user bits and canonical
  W2/W3 stores only W0+W1 (8 bytes). The flip bit is encoded in the marker (0xFD vs 0xFE).
- **No boxes stored**: rebuilt from vert21 vertices during deserialization.
- **No skip words stored**: implied by the tree shape.
- **Verts written BEFORE tree**: so deserializer can rebuild leaf boxes during tree pass.

The tree is a **pre-order forest** of the suppressed root's top-level children.

Source: `prog/engine/daBVH/bvhIO.cpp`, function `writeSubtree()`

### 10.2 What the reader does

The reader (either `Rebuilder::node()` for stackless or `Soa4Deserializer::parseNode()`
for SoA4) recursively reads the tree, rebuilding bounding boxes from the vertex data.
It reconstructs the full stackless or SoA4 format in memory.

The reader distinguishes leaf from internal by the 1-byte marker: values
`{0x00, 0xFD, 0xFE, 0xFF}` are leaves, everything else (`1..252`) is an internal node.

Source: `prog/engine/daBVH/bvhIO.cpp`, functions `bvhIoIsLeafMarker()`, `bvhIoReadLeafRecord()`

### 10.3 Recursive Parser (IMPLEMENTED - 100% face recovery)

The recursive descent parser reads the tree exactly as the engine does:

```python
LEAF_MARKERS = {0x00, 0xFD, 0xFE, 0xFF}

def read_node(data, ofs, depth):
    cc = data[ofs]; ofs += 1
    
    if cc in LEAF_MARKERS:
        # Read leaf body (8 or 16 bytes depending on marker)
        if cc == 0xFE or cc == 0xFD:    # short leaf
            w0, w1 = unpack('<II', data, ofs); ofs += 8
            w2 = QUAD_FLIPA_FLAG if cc == 0xFD else 0; w3 = 0
        else:                            # full (0x00) or raw (0xFF)
            w0, w1, w2, w3 = unpack('<IIII', data, ofs); ofs += 16
        
        # W1 bits[0:23] = vertex INDEX (not byte offset!)
        base = w1 & 0xFFFFFF
        # Decode faces from quad leaf fields (same as expandQuadLeafTris)
        ...
    else:
        # Internal node: cc = child count (1..252)
        for _ in range(cc):
            read_node(data, ofs, depth + 1)
```

**Forest loop stop condition**: Each leaf adds 28 to a virtual stackless offset,
each internal adds 16. The forest loop runs until the virtual offset reaches
`BlasIoHeader.treeBytes` (the stackless tree size from the header).

### Results for a_4b_collision:

```
57 mesh nodes
5,074 vertices (100% decoded from vert21)
8,565 faces of 8,565 expected (100.0%)
```

---

## 11. In-Memory Formats (for reference)

### 11.1 Stackless BVH Node (16 bytes, in reconstructed tree)

```
Bytes 0-3:   uint32 = minX(16) | maxX(16)  (quantized bbox)
Bytes 4-7:   uint32 = minY(16) | maxY(16)
Bytes 8-11:  uint32 = minZ(16) | maxZ(16)
Bytes 12-15: uint32 = skip word
  bit 31 clear: internal node, skip = bytes to jump on miss
  bit 31 set:   leaf node (W0 of double-quad)
```

Internal nodes: 16 bytes. Leaf nodes: 28 bytes (16 header + 12 body = W1+W2+W3).

### 11.2 NodeBlasChunkHeader (48 bytes, in memory only)

```
Offset  Size  Field
0       12    float[3] scale (from CollResChunkFrame)
12      12    float[3] invScale (from CollResChunkFrame)
24      12    float[3] bmin (from CollResChunkFrame)
36      4     uint32 treeBytes (from deserialize result, NOT on wire)
40      4     int32 rootRef (from deserialize result, NOT on wire)
44      4     uint32 flags (bit 0 = HAS_EDGE_FLAGS, NOT on wire)
```

### 11.3 In-memory chunk layout:

```
[NodeBlasChunkHeader: 48 bytes]
[SoA4 tree: treeBytes]
[padding to 8-byte alignment]
[vert21 stream: vertCount * 8 bytes]
```

---

## 12. Name Resolution

Node names are null-terminated strings in the NAMES byte array.
Each CollResNodeRec has a `nameOfs` field pointing into this array.

```python
def get_name(names_data, offset):
    end = names_data.find(b'\x00', offset)
    return names_data[offset:end].decode('utf-8')
```

Material names are in MAT_NAMES array, referenced by `physMatId`.

---

## 13. Summary of Key Constants

```
CollisionGameResClassId   = 0xACE50000
COLLRES_STREAM_VERSION    = 3
BlasIoHeader magic        = 0x694C4264 ('dBLi')
BVH_BLAS_LEAF_SIZE        = 28 (stackless leaf)
BVH_BLAS_NODE_SIZE        = 16 (stackless internal node)
BVH_BLAS_VERT21_STRIDE    = 8
QUAD_LEAF_FLAG            = 1 << 31
QUAD_FLIPA_FLAG           = 1 << 29
QUAD_FLIPB_FLAG           = 1 << 30
QUAD_BASE_MASK            = 0xFFFFFF
QUAD_O_BITS               = 13
Wire markers:
  FULL_LEAF               = 0x00
  SHORT_LEAF              = 0xFE
  SHORT_LEAF_FLIP         = 0xFD
  RAW_LEAF                = 0xFF
  INTERNAL                = 1..252 (child count)
```

---

## 14. Code Changes Made to Dagor-Asset-Explorer

### 14.1 `src/dae/parse/realres.py`

- Line ~2370: Relaxed magic check to compare upper 16 bits only:
  `(magic & 0xFFFF0000) != (self.classId & 0xFFFF0000)`
- Added `CollNode.initFromV3()` method for v3 format nodes
- Added `__readFileV3__()` method with zstd decompression and stream parsing

### 14.2 `src/dae/parse/gameres.py`

- Line ~205: Added fallback lookup with masked classId:
  `maskedClassId = classId & 0xFFFF0000` tried when exact match fails

---

## 15. Working Extraction Script

The file `extract_collision_v3.py` (project root) implements:
1. GRP parsing
2. Zstd decompression of collision data
3. Stream header + node records + transforms parsing
4. BLAS chunk boundary detection via BlasIoHeader magic scanning
5. Vert21 decoding (100% correct)
6. **Recursive wire-tree parser** for face extraction (100% recovery)
7. Export to OBJ and DMF formats

### Final results for a_4b_collision:

```
57 mesh nodes (matches Asset Viewer: "57 meshes")
5,074 vertices (100% decoded from vert21)
8,565 faces of 8,565 expected (100.0%)
```

### Key files:

- Script: `E:\Dagor Asset Explorer\extract_collision_v3.py`
- Output OBJ: `E:\Dagor Asset Explorer\output\a_4b_collision.obj`
- Output DMF: `E:\Dagor Asset Explorer\output\a_4b_collision.dmf`
- GRP file: `E:\Dagor Asset Explorer\grp\usa_aircraft_logic.grp`

---

## 16. Engine Source References

All code was verified against the open-source Dagor Engine:
https://github.com/GaijinEntertainment/DagorEngine

| File | Path | Key content |
|------|------|-------------|
| `bvhIO.cpp` | `prog/engine/daBVH/bvhIO.cpp` | Serialize/deserialize implementations |
| `dag_bvhIO.h` | `prog/dagorInclude/daBVH/dag_bvhIO.h` | BlasIoHeader, wire format documentation |
| `dag_swBLAS_leaf.h` | `prog/dagorInclude/daBVH/dag_swBLAS_leaf.h` | Leaf decode, QuadLeafFields, expandQuadLeafTris |
| `swBLASLeafDefs.hlsli` | `prog/dagorInclude/daBVH/swBLASLeafDefs.hlsli` | Bit field constants |
| `dag_swBLAS_soa4.h` | `prog/dagorInclude/daBVH/dag_swBLAS_soa4.h` | SoA4 tree layout constants |
