# Addendum — LVL post-vertex-array data (two sessions)

Picks up recommended next step #1 from the original report ("LVL FACES").
Corpus: all 54 `.lvl` files found across the newly-handed-over MaxED
install (`levels/`, `levels/testlevels/`, `levels/observations/`,
`levels/prefabs/`, `prefabs/`, `Examples/BasicRoom.lvl`), 486 objects total
that expose the header below.

## CONFIRMED — the header and its shape

For many objects (not just simple boxes — see correction below), the
vertex array is immediately followed by:

```
u32           count            (6 for every object seen so far)
u32           second_field      (4 for every object seen so far)
f64 x 3 x count   a run of `count` 3-double vectors
```

Detection method: read the header, then require *every one* of `count`
consecutive 3×f64 triples to have magnitude 1.0 within 2%. Coincidental
bytes essentially never produce N-for-N unit vectors, so hits are
trustworthy. New tool command: `python3 lvl_geom.py faces <file.lvl> [...]`.

**486 objects across the full 54-file corpus expose this header with
count=6.** Of those, 389 pass the strict all-magnitude-1 filter.

## CORRECTED — this is not "6 face normals of a hexahedron"

That was the working theory at the end of the last session, based on a
small sample of axis-aligned boxes. Re-tested against the **full corpus**,
including several genuinely rotated/beveled objects, and it doesn't hold.

**351/389 (90%) of all count=6 objects match this exact 6-slot pattern,
verified by direct numeric equality (not just "looks axis-like"):**

```
[ N, -N, (1,0,0), (0,1,0), (0,0,1), N ]
```

— a direction vector, its exact negation, a **fixed 3x3 identity
matrix**, then the same vector a third time.

Confirmed on rotated geometry, which is what falsifies the old "6 unique
face normals" and "orientation matrix" readings at once:
- `Lightbox` (`graphic_novel_view.lvl`) is a visibly beveled wedge;
  its `N = (0.259, -0.966, 0)`, matching the bevel's actual slope in the
  vertex data.
- Several `New mesh NN` trim pieces in `observation_06.lvl` are similarly
  beveled; `N = (-0.949, -0.316, 0)` and `(-0.894, -0.447, 0)` in two
  different objects, again matching each one's own slope.
- In **every one of these rotated cases the "identity matrix" slots stayed
  exactly `(1,0,0),(0,1,0),(0,0,1)` regardless of the object's true
  orientation** — ruling out "per-object orientation matrix" as the
  meaning of those 3 slots. They read as boilerplate/placeholder, not
  derived from geometry. It's `N` (and its negation) that carries real,
  verified per-object information.

The remaining 38/389 non-matches are mostly sign/degenerate edge cases
(e.g. `N` and `-N` both landing on the same axis because no true opposite
face was found) rather than evidence of a different layout — consistent
with the degenerate-vector fallback found in the disassembly below.

**Open question this leaves:** what `N` actually represents (a single
face normal? an edge/bevel direction? something CSG-operation-specific?),
and — if this object really is a hexahedron with 6 distinct faces —
where the *other* 4 face normals live, since only one direction (plus its
negation) appears in this record at all.

## FOUND — the real per-polygon Serialize loop (this is the one that matters)

Went back to `MFC42.DLL`'s import table instead of guessing blind. It's
imported by ordinal, and this particular `MFC42.DLL` build doesn't embed
names — but Ghidra ships a full ordinal→name table for it
(`NationalSecurityAgency/ghidra`, `.../data/symbols/win32/mfc42.exports`,
fetched from GitHub). Cross-referencing `MaxED.exe`'s import ordinals
against that table resolves every MFC call by real name.

That immediately surfaced `CArchive::Write` (ordinal 6383) and
`CArchive::Read` (ordinal 5440) in the import table — **real MFC
serialization**, not raw `WriteFile`. `CArchive::Write`'s IAT thunk
(`0x6486EE`) has only **12 call sites total** in the whole binary — far
more tractable than tracing UI call chains.

**One of those 12 call sites is inside a genuine loop over the mesh's
`E_Polygon` doubly-linked list** (`ecx=[edi+0x322]`, the same list field
`E_Polygon::export` walks — confirms both functions operate on the same
polygon list). Function containing the loop: `~0x4E9F50`–`0x4EA290`.
Per polygon, in order, it writes:
1. **one raw 32-bit field** (via the manual buffer-growth-check pattern
   also seen elsewhere — capacity check against `[archive+0x28]`, `call
   0x6486F4` to grow, then a direct `mov [ptr], value`),
2. **3 doubles**, via three chained calls to `0x447700` — a small local
   helper, fully read: takes an 8-byte double as two stack DWORDs, does
   the same buffer-growth check, writes the 8 bytes directly
   (`mov [ptr],lo` / `mov [ptr+4],hi`), advances the write pointer by 8,
   returns the archive pointer (enabling the `a<<b<<c` chaining pattern
   visible in the disassembly). This is confirmed to write **raw,
   unmodified doubles** — no conversion, no scaling — which is exactly
   what a byte-for-byte file parser should expect.
3. **3 more sub-fields**, passed to different helper functions
   (`0x45B8C0`, `0x4FE2D0`, `0x4FF610`) — not yet identified, but by
   position these are extra per-polygon data written immediately after
   the 3-double block (candidates: material index, UV/lightmap info,
   or a nested sub-object).

This is a substantially better match for the actual `.LVL` writer than
`E_Polygon::export` (previous section) — it goes through `CArchive`, MFC's
standard document-persistence mechanism, which is what a native scene
format would use, rather than the ad-hoc buffer-and-flag-gated pattern
`::export` uses for what looks like the compiled-LDB path.

**Open thread, not yet resolved:** this loop writes **one leading 32-bit
field per polygon before its 3 doubles**, which our external byte-level
scan didn't account for (we found a clean, constant `[N,-N,+X,+Y,+Z,N]`
run with no interposed per-vector field). Two ways to reconcile this next
session: (a) re-scan the file bytes allowing an extra leading field
per 3-vector group and see if a per-group u32/f32 was actually present
and previously mis-absorbed into the "header", or (b) this `CArchive`
loop and the on-disk `.LVL` records aren't the same call after all (there
are 11 other `CArchive::Write` call sites — worth checking whether a
different one is the real match). Either way, the write primitive itself
(`0x447700`, confirmed to write raw doubles unmodified) is solid ground
to build on.

**Method note for next time:** MFC-ordinal resolution is now unblocked —
`mfc42.exports` (from Ghidra's repo) plus `pefile`'s
`DIRECTORY_ENTRY_IMPORT` on `MaxED.exe` gives IAT-address→real-name for
every MFC42 call in the binary in a few lines of Python. This should be
done at the *start* of any further binary work, not resorted to after
several dead ends, since every future lead through MFC (dialogs,
serialization, collections) benefits.

## Continued this round — ruled out the polygon-loop candidate, found a much stronger one

**Ruled out:** fully disassembled the two "extra sub-field" helper
functions from the per-polygon loop above (`0x45B8C0` confirmed, plus
its neighbors by the same pattern). They are **not** no-ops — `0x45B8C0`
alone writes 3 more raw doubles (24 more bytes), and there are two
further calls of the same shape after it. That means this per-polygon
loop writes far more than the clean, gap-free `[N,-N,+X,+Y,+Z,N]` run we
verified on disk — a real polygon entry here would leave visible extra
bytes between vector groups, which we don't see. **This loop is real
`CArchive`-based serialization on the same `E_Polygon` list, but it is
not the source of the on-disk pattern.** Most likely explanation: MFC
apps of this era commonly reuse `CArchive` for clipboard/OLE-drag-data
and undo snapshots as well as file save; this is probably one of those,
not the file writer.

**Found a much better candidate.** Of the remaining `CArchive::Write`
call sites, one (`0x54C735`, inside a function starting at `0x54C510`)
sits in code with a distinctive, textbook shape:
- Reads a flag field and branches on a single bit early on
  (`mov ecx,[esi+0x14] / not ecx / test cl,1 / je <loading-path>`) — the
  classic compiled form of MFC's `if (ar.IsStoring()) {...} else {...}`.
  The "else" target (`0x54C858`) is a **separate, unexplored loading/read
  path** — strong evidence this whole function is a genuine
  `Serialize(CArchive&)` override, not a one-off export routine.
- Builds and writes a **flags DWORD** derived from the object's own
  state (bit `0x10` set exactly when a count field `[obj+0x600] > 0`).
- Writes a **constant `0x14` (20 decimal)** as a per-object field
  immediately after — shape-consistent with an MFC `IMPLEMENT_SERIAL`
  schema/version number (written automatically by the serialization
  macros), not geometry data.
- Calls out to several sub-object serializers at increasing offsets
  (`obj+0x582`, `obj+0x5B2`, `obj+0x4A0`, `obj+0x54`, ...) — very likely
  including a name string (matching the `.LVL` format's confirmed
  length-prefixed name fields) and nested objects.
- Finally, gated behind that flags bit, writes a **generic dynamic
  array: `u32 count` followed by, if the data pointer is non-null, a raw
  block of `count*4` bytes** (`shl edx,2` computing the byte length,
  then a real `CArchive::Write(ptr, count*4)` call — this is the call
  site originally found). This is a completely generic
  "array-of-4-byte-elements" serialization shape and could plausibly be
  an index list, a material/id array, or something else — not yet pinned
  down to our specific 6-vector geometry data.
- No static callers found for this function at all (`0x54C510` doesn't
  appear as an immediate anywhere in the binary) — consistent with it
  being reached only through a **virtual call**, i.e. `CObject::Serialize`
  being overridden and invoked polymorphically (e.g. from a collection's
  own `Serialize`, which is exactly how MFC serializes a document made of
  heterogeneous objects). Located its **vtable** by searching for
  cross-references to the function's true entry point (`0x54C510`,
  found by walking back through the SEH prologue) — exactly one hit, at
  file offset `0x278260` in `.rdata`, sitting among a mix of local
  function pointers and MFC default-implementation IAT thunks (typical
  partially-overridden MFC vtable shape).

**Assessment:** this function is a substantially better match for a
genuine top-level document/object `Serialize()` than anything found
before it (the `IsStoring()` branch alone is close to decisive), but it
has **not yet been matched byte-for-byte to our on-disk 6-vector finding**
— the final `[count][count*4 bytes]` block is generic and could be
several different things. The immediate next step is tracing the earlier
sub-object calls this function makes (`obj+0x54`, `obj+0x5B2`, `obj+0x4A0`)
to see if one of them is where our vertex array / 6-vector pattern
actually gets written, rather than assuming it's the final array shown
here.

**Traced one sub-call (`obj+0x54`, `0x526270`) — turns out to be generic
object-pointer serialization, not geometry.** It writes two raw values
pulled from fixed global addresses (very likely a class-identification
tag and a schema number — matching MFC's standard mechanism for
serializing a polymorphic `CObject*`, `CArchive::WriteObject`/
`CRuntimeClass::Store`), then makes a **virtual call** through the nested
object's own vtable (`[[ebx]][+0x34]`) to invoke *that* object's own
`Serialize`. This confirms the document is a real polymorphic object
tree (consistent with the `E_Object`/`E_Mesh`/`E_Polygon` hierarchy found
earlier) but this particular sub-call is infrastructure, not where our
vector data lives — the search for the actual geometry-writing call
needs to follow that virtual dispatch one level deeper, which wasn't done
this session.



## This round — narrowed to a short, specific list of candidates

**Key structural finding:** searched the *entire* disassembly for every
call site to the double-write helper (`0x447700`) and grouped them by
how many fire consecutively in the static code. There is **no run of 6**
anywhere in the binary — only runs of 1, 2, or 3. This proves the on-disk
6-vector block can't come from one function writing 6 doubles in a row;
it has to be a **loop that fires a 3-double-writing routine exactly
twice**. That's a real, useful constraint: it also fits the corpus
finding that `count` in the header is always `6` — if `count` is really
a loop-iteration count (e.g. "number of polygons/parts", here always 2
for these primitives) times 3 doubles per iteration... except the header
literally reads `6`, so either the header counts vectors directly (not
iterations) or there's a factor-of-3 relationship not yet nailed down.

That search produced **9 total candidate call sites** for the "write 3
doubles" pattern (`0x45B1DC`, `0x4DE914`, `0x4E9C05`, `0x4E9C32`,
`0x4E9D8F`, `0x4EA164`, `0x4EA23C`, `0x5096A0`, plus the one already
examined in the `E_Polygon` loop). Checked the code immediately
following each one: **every single candidate is followed by more writes
in the static code** — either more doubles (calls to `0x45B8C0`, the
same "3 more doubles" helper from the ruled-out `E_Polygon` loop), raw
scalar field pushes, or a conditional branch on a global flag (one,
`0x4E9C32`, branches on a byte at `ds:0x6E15D0` — probably a
"use-vertex-colors"-style global option — between writing a default
`(0,0,0,1)`-shaped constant and writing real per-object data). None
shows a statically clean "3 doubles and nothing else" shape.

**Assessment, honestly:** this doesn't necessarily rule these out — for
our clean on-disk pattern to arise from any of them, whatever follows
the 3 doubles in that particular function must be conditionally skipped
at runtime for these specific simple/untextured box primitives (very
plausible, since several of these look like generic per-polygon
Serialize routines with optional features — secondary UVs, vertex
colors — that a plain box wouldn't use). But confirming *which* of the
9 and *why* the trailing writes vanish requires knowing the runtime
value of flags/counts we can't see from static bytes alone. This is the
point where static disassembly has hit real diminishing returns without
a live debugger to set a breakpoint and watch actual writes happen —
not available in this environment (no Windows/Wine/GUI here).

**Concrete shortlist for next time**, in case a debugger becomes
available or further static tracing is worth it:
`0x45B1DC`, `0x4DE914`, `0x4E9C05`, `0x4E9C32`, `0x4E9D8F`, `0x4EA164`,
`0x4EA23C`, `0x5096A0` — all confirmed to write exactly 3 chained
doubles via `0x447700`; the header/count relationship above narrows this
further if useful.

**Also mapped this round (useful context, not directly on the
critical path):** the `IsStoring()` function's earlier sub-object calls.
`obj+0x582` is a genuine nested-collection serializer (count, then a
linked-list walk writing a string plus a nested block per element,
recursing into a further sub-list) — this lines up well with the
already-confirmed external material-table structure (named entries,
each referencing further nested texture data), corroborating rather than
locating our target. `obj+0x4A0`'s "else" branch (`0x58A910`) writes a
long flat run of individual scalar fields (12+ separate `DWORD`/float
pushes) with no count-driven loop visible in the portion traced — shape
suggests a fixed-size parameter block (e.g. camera/light settings), not
a variable-length vertex or polygon array. Lower priority for next time.

## BREAKTHROUGH — a third-party LDB→LVL Java converter exists, decompiled and validated live

The person handed over `ldb-to-lvl.jar` (author: artkuznet). Decompiled
cleanly with the CFR decompiler (`java -jar cfr.jar ldb-to-lvl.jar
--outputdir ...`, zero errors) — full source recovered for every class.
This is an independent, working, **one-directional LDB → LVL converter**
(no LVL reader). Confirmed by running it directly (`java -jar
ldb-to-lvl.jar exit_test_01.ldb`) and then **opening the result in our
live Wine-hosted MaxED — it loaded with zero errors**, correct 5-room
tree (`StartRoom`, `LeftRoom01/02`, `RightRoom01/02` — exactly matching
the tool's own console output), and correct materials
(`TRAINFLOOR01.JPG`, `SLIDINGDOORS02...`). This is about as strong a
validation as static analysis can get without Remedy's own source: a
real, independent implementation whose output MaxED itself accepts.

**Caveat worth keeping in mind:** this tool's own field names —
`unkVector1`, `unkTransform`, `unk11`, `getUnknownVectors()` — are
honestly labeled "unknown" by its own author. It's a working
reimplementation good enough to produce loadable files, not a source of
Remedy's original semantic ground truth. Treat it as very strong
corroborating evidence and a huge documentation shortcut, not an oracle.

### The full per-polygon LVL record, straight from working source (`LVL.java`)

This resolves nearly everything documented as "still unknown" above. Per
mesh, in order:

```
byte     flipFaces flag
7 bytes  padding/reserved (zero)
u32      vertex count
f64×3×N  vertices (room-local space)
u32      polygon count                    <-- matches our external header field 1:1
for each polygon:
  u32      edge count                     <-- explains our external "vpf" reading:
                                               it's actually the FIRST polygon's own
                                               edge count, not a mesh-level field
  vec3     normal                         (real geometric face normal)
  vec3     unkVector1                     (= normal snapped to nearest axis; see below)
  vec3×4   "transform": [1,0,0],[0,1,0],[0,0,1], normal   <-- the fixed identity
                                               block we found is CONFIRMED as literally
                                               hardcoded constants, not derived data
  vec3     unkVertex
  vec3     scaleU
  vec3     scaleV
  vec3     normal (again)
  f64      textureOffset[0]
  f64      textureOffset[1]
  vec3     unkVertex (again)
  u32      0
  4 bytes  color (r,g,b,a)
  f64      light intensity
  f64      lightmap resolution
  u32+i16  exit-linkage fields (varies if this polygon is a room Exit)
  2 bytes  (flags, exit-dependent)
  u8+str   material name (length-prefixed)
  u8+str   bitmap/texture name (length-prefixed; +1 extra zero byte if empty)
  per edge: u32 from, u32 to
  u32      triangle count
  per triangle: vec3 normal, u32 vertex count, u32×N vertex indices
  vec3     unkVector1 (again)
  vec3     (0,0,0)
  vec3×2   getUnknownVectors() — 2 more fixed vectors, chosen from a small
                                  lookup table keyed on unkVector1's sign
                                  (see below — NOT simply ±normal)
  vec3     unkVector1 (yet again)
  vec3     (0,0,0)
  vec3     (0,0,0)
  ... (more scalar fields follow: ints 4,4, doubles 1.0,1.0, int 66,
       shorts 0,2,0, ...; not fully transcribed here — see the attached
       LVL.java for the complete, exact sequence)
```

**This directly explains the "header" we spent two sessions on.** What
we called `[u32 face_count][u32 vpf]` is actually `[mesh.polygon_count]`
followed immediately by `[polygon[0].edge_count]` — not two mesh-level
fields at all, just the count and then the very next per-polygon record
starting. And the "6 clean vectors" we isolated externally is the
`normal, unkVector1, transform-row0, row1, row2, row3(=normal)` run for
**one polygon** — our external magnitude-1 filter happened to stop
right where `unkVertex`/`scaleU`/`scaleV` (non-unit-magnitude) broke the
pattern, which is exactly why it looked like a clean, self-contained
6-vector block.

**The identity submatrix mystery is fully solved:** `getDefaultTransform()`
literally returns `{{1,0,0},{0,1,0},{0,0,1},{normal.x,normal.y,normal.z}}`
as hardcoded Java constants when no other transform is set — confirming,
from real source, exactly what the corpus scan found empirically (always
identity, never varies with rotation, because it isn't derived from
rotation at all).

**One open discrepancy, noted honestly rather than papered over:** by
this tool's logic, `unkVector1` (`getDefaultUnk5()`) is the normal
snapped to its nearest axis, which for an already axis-aligned face
should equal the normal exactly — predicting a `[N, N, +X, +Y, +Z, N]`
pattern. Our external corpus instead measured `[N, -N, +X, +Y, +Z, N]`
(exact negation at position 1, verified over hundreds of objects). Since
this tool is a reimplementation rather than Remedy's original code, the
likely explanation is a winding/sign convention difference between how
this tool computes normals during LDB→LVL conversion versus how
Remedy's own MaxED originally computed and stored them — not a flaw in
either party's data. Worth keeping in mind rather than trusting either
source blindly on this one specific sign.

### Also recovered, straight from source (bonus — not just the mystery vectors)

- `getUnknownVectors()`'s exact lookup table (6 branches, checked in
  strict X→Y→Z priority order on `unkVector1`'s sign, each returning 2
  fixed axis-aligned vectors) — a complete, literal spec for that field,
  reproduced in `LvlPolygon.java` (attached).
- The **entire rest of the polygon record** — triangle list format,
  material/bitmap name encoding, exit linkage, lightmap resolution,
  color — is now known exactly, not just the vector block. This is
  effectively a full LVL polygon-write specification for anyone building
  an LVL writer or reader from scratch.
- Confirms mesh-level vertex array format exactly as external analysis
  already had it: `u32 count` then `f64×3×count`, room-local space.

### Files handed back this round
- `ldb2lvl_decompiled/LVL.java`, `LvlPolygon.java`, `VectorCalculator.java`,
  `Mesh.java` — full decompiled source, primary reference for any future
  LVL reader/writer work.
- `exit_test_01_from_jar.lvl` — the tool's own output, confirmed loading
  cleanly in real MaxED, useful as a clean reference file going forward.

## LIVE GUI VALIDATION — MaxED actually runs, confirmed under Wine

Big development, courtesy of the person's own hands-on MaxED experience
(exact launch sequence, rendering-device choice, and texture-mode controls
all confirmed against a real working setup): **`MaxED.exe` runs correctly
under Wine in this environment**, given the right setup:

- `dpkg --add-architecture i386` + `apt-get install wine32:i386`, a
  `WINEARCH=win32` prefix, Xvfb + a window manager (fluxbox) for a
  virtual display.
- MaxED hardcodes its data path as `D:\Projects\MaxPayne\MaxPayne\exe\...`.
  Fixed by symlinking a fake `D:` drive
  (`dosdevices/d: -> /root/fakedrive_d/Projects/MaxPayne/MaxPayne/exe ->`
  the real MaxED folder) rather than fighting the hardcoded path.
- MaxED also insists every `Data\DATABASE\<Category>` subfolder exist
  (though can be *empty* — confirmed once the folder merely exists,
  MaxED silently treats missing content as "database incomplete" and
  moves on, it does **not** hard-block). No real sound/text/etc. assets
  needed, just the directories themselves.
- Launch sequence, confirmed matching the person's own workflow exactly:
  splash → "Select Rendering Device" (DX7 Direct3D Onboard HW T&L,
  Reference optimizations) → blue grid → File > Open.

**Successfully opened multiple real `.lvl` files** (`ai_test_01.lvl`,
`BasicRoom.lvl`, and — per the person's own screenshot — `Manor_Outside.lvl`,
a large, fully-lit outdoor scene) with correct geometry, materials, and
room structure. This is a strong, independent validation that our
understanding of the `.LVL` format (vertex arrays, material table, room
structure) is fundamentally sound — MaxED parses these files cleanly
end-to-end with no errors.

### Direct confirmation of the header's first field = polygon count

Selected simple box objects (`New mesh 00` in `BasicRoom.lvl`,
`CameraController_01` in another file) and opened their real
**Properties > Statistics** dialog in the running app:

| Object | Polygons | Triangles | Vertices |
|---|---|---|---|
| `New mesh 00` (box) | 6 | 12 | 24 |
| `CameraController_01` (box) | 6 | 12 | 24 |
| `Mesh_624` (non-box) | 7 | 16 | 30 |

**This directly confirms, from the tool's own UI, that our externally-derived
header's first field (always `6` for the box objects we'd analyzed) really
is the polygon count** — not a coincidence of box topology, but the actual
semantic meaning, cross-checked independently of any file-byte guessing.
The "24 vertices" (vs. our raw parser's 8 unique corner positions) is the
*triangulated/render* vertex count — each polygon needs its own vertex
copies for correct per-face UV/normal data, which is a completely normal
and expected difference between the compact source format and the
renderable mesh, not a discrepancy.

### Per-polygon context menu (Texture mode, middle-click) — real field vocabulary

In Texture mode (F6), left-click selects a single polygon face; middle-click
opens a full context menu of real per-polygon operations. This is the
actual, confirmed vocabulary MaxED uses internally for per-face data:

```
Render polygon
Scale U axis (U)      Scale V axis (V)
Flip U (X)             Flip V (Y)
Texturize mesh (T)     Texturize w/ default scale (Ctrl+T)
Get default tiling
Set light color (L)    Set default light (Shift+L)
Set light intensity (I)
Set lightmap resolution (K / Shift+K)
Set lmres by normal    Colorize lightmaps
Join polygons (J)      Copy mapping (C)   Get material (G)
Fit edge w/ aspect (F) Create T-vertices (Shift+T)
Copy/paste lightmap
Blending >   Moving >   Planar map mode (N)
Display debug information
```

All live status-bar readouts tied to these tools (`txl (x,y)`,
`sp (u,v)`, `PLANAR MAP txl (...)`) are **texel/UV coordinates**, not raw
plane-equation or normal values — the UI is artist-facing (texture
alignment) rather than a raw data inspector. **This is a meaningful
negative result for the "6-vector" investigation**: it doesn't hand us
`N`'s value directly, but the presence of "Scale U/V axis", "Flip U/V",
and especially **"Planar map mode"** as real, dedicated per-polygon
concepts is good corroborating evidence for the standing hypothesis that
`N` (our repeated, non-axis-aligned-on-rotated-objects vector) is a
**texture projection axis** rather than a pure geometric face normal —
exactly the kind of per-polygon data these menu items operate on.

### Next steps this makes possible

- A real debugger can now plausibly be attached to this Wine process
  (`winedbg`) to set a breakpoint on the `.LVL` save path and watch it
  live — this was the exact wall static disassembly hit last round.
  Not attempted yet this session.
- "Display debug information" is a toggle (confirmed, checkbox in the
  menu) — worth a follow-up look at whether it draws an on-screen
  overlay near the selected polygon (didn't manage to spot one in the
  screenshots taken, may need a closer camera view of a small, isolated
  object rather than a room wall).
- Environment is fully set up and persists on disk
  (`/root/.wine32`, `/root/fakedrive_d`, symlinked `Data\DATABASE\*`) —
  future sessions can relaunch directly via:
  ```
  Xvfb :99 -screen 0 1280x900x24 &
  DISPLAY=:99 fluxbox &
  WINEARCH=win32 WINEPREFIX=/root/.wine32 DISPLAY=:99 \
    wine "D:\Projects\MaxPayne\MaxPayne\exe\MaxED.exe"
  ```

## `E_Polygon::export` disassembly — corroborates the shape, not the file

The MaxED install includes `MaxED.exe` (3.8 MB PE32, unstripped C++
mangled names/RTTI intact, `objdump -M intel -D` disassembles it cleanly).
This turned out to be a much stronger source of ground truth than guessing
from file bytes alone.

**Recovered real engine class names** (replaces this report's earlier
placeholder terms): `E_Object` → `E_Mesh` → contains `E_Polygon`,
`E_Vertex`, `E_Edge`, each held in a doubly-linked list
(`R_DLListBase<E_Polygon>`, etc.) — a genuine polygon-mesh structure, not
the raw brush/CSG model guessed at previously. Also present:
`E_MeshEdit`, `P_TriangleMesh`, `M_Plane3Template<N>` (a plane-equation
utility class, seen elsewhere — e.g. camera clip planes — not yet
confirmed as this on-disk format).

**Found and disassembled `E_Polygon::export`, `0x4EB602`–`0x4EB896`.**
Located by searching `.text` for a `push` of the absolute VA of its
diagnostic string `"E_Polygon::export; solid poly without lightmap!"`
(string at file offset `0x2C905C`, VA `0x6C905C`) — one hit, at `0x4EB8D9`,
inside this function.

What the disassembly shows directly (not inferred from file bytes):
- Walks the mesh's polygon doubly-linked list (`ecx = [ebx+0x322]`) —
  one pass of the relevant code per polygon.
- **Normalizes vectors 3 at a time** (loop counter `edx=3`) in a tight
  FPU sequence: dot each vector with itself, compare against an epsilon,
  `fsqrt` + reciprocal — genuine runtime normalization. This is exactly
  why decoded values carry floating-point noise (`2.98e-14` etc.) instead
  of being hand-typed axis constants.
- **Degenerate-vector fallback, decoded exactly:** on failing the epsilon
  test, the code overwrites just the vector's first double with the
  literal constant `0.001` (`mov [ecx],0xd2f1a9fc` / `mov [ecx+4],
  0x3f50624d`, which is exactly `0.001` as an IEEE double).
- Each vector record advances by `0x18` (24) bytes — matches the
  externally-observed stride exactly.
- Afterward, gated behind a flag test (`test ds:0x6D97F8, 0x10000`), the
  function conditionally reads 4 more 32-bit fields into consecutive
  output slots — shape matches "4 lightmap/UV corner values", consistent
  with the function's own "solid poly **without lightmap**" message.

**Important caveat, unresolved:** the name `E_Polygon::export` and the
lightmap-specific warning suggest this is the LDB *compile* path (builds
the game-ready level), not the code that writes `.LVL` itself. It's
strong corroboration that "3-vector groups, normalized, with a
degenerate fallback" is a real engine concept — matching our 24-byte
stride and the `N, -N, N` pattern's floating-point noise — but it
shouldn't be assumed to describe the `.LVL` on-disk layout byte-for-byte.
The actual `.LVL` writer is still a different, untraced function (see
below).

### Earlier leads, superseded

- `WriteFile` (kernel32) has exactly one call site (`0x545275`), and it's
  just a generic "write a null-terminated string to a file" helper — not
  the binary polygon writer. This dead end is what motivated resolving
  MFC ordinals (above), which then found the real `CArchive`-based path
  directly. No need to revisit this.
- File-dialog filter string `"Max Payne Levels|*.lvl||"` (VA `0x54823E`):
  traced the containing function, `0x548220`–`0x548364`. It's just the
  "browse for a level file" helper — constructs and runs an MFC
  `CFileDialog`, returns the chosen path. Not the writer itself, and its
  caller wasn't traced further.
- Extension string `.LVL` (VA `0x403FDF`): traced the containing function,
  `0x403F70`–`0x404041`. This is a directory-listing routine — enumerates
  a folder (`FindFirstFile`/`FindNextFile`-style loop), filters for names
  ending in `.LVL`, and builds a list. This is the "populate the levels
  list" UI code, not the saver.
- Extension string `.lvl` (VA `0x5603C8`) — not yet traced.

**Method used, worth reusing:** `data.find(known_string)` for a string's
file offset; since this binary's file offsets equal virtual addresses in
every section (no rebasing needed), search `.text` for the 4-byte
little-endian value `0x400000 + offset` to get direct instruction-level
cross-references, far faster than reading the whole disassembly.

## STILL UNKNOWN

- What `N` (and `-N`) actually mean semantically, and where the rest of
  a hexahedron's face data would be if this record only carries one
  direction.
- What immediately follows the 6-vector block — more float64s of similar
  shape, not yet matched against expected values (a first attempt at
  matching plane distances on a `Crates.LVL` box didn't line up).
- Face/vertex connectivity — still not located.
- Why many non-"count=6" objects don't show this header right after their
  vertex array — presumably an undecoded per-object block (material ref?
  transform? CSG operator?) of variable size sits in between, matching
  the open item in section 2.3d of the main report.
- Ruled out: the `E_Polygon`-list `CArchive::Write` loop (leading 32-bit
  field + 3 doubles + 3 more sub-fields per polygon) — confirmed it
  writes too much data per iteration to match the clean, gap-free
  `[N,-N,+X,+Y,+Z,N]` pattern. Not the source of our on-disk finding;
  likely a clipboard/undo consumer of the same polygon list.
- The strongest candidate so far (`0x54C510`, the `IsStoring()`-branching
  function) still needs its earlier sub-object calls
  (`obj+0x582`, `obj+0x5B2`, `obj+0x4A0`) traced — one of those, not the
  final `[count][count*4]` array, is the more likely home for our vector
  data. `obj+0x54` was traced and ruled out (generic polymorphic
  object-pointer serialization, not geometry). `obj+0x582` traced and
  looks like the material table (corroborates known structure, doesn't
  locate the target). `obj+0x4A0`'s common-case branch looks like a flat
  scalar parameter block, not variable-length geometry — low priority.
- The 9-candidate shortlist for the actual 3-double writer (see above) —
  narrowing further needs either runtime flag values (a debugger) or
  more corpus-side correlation (e.g. find an object where the "extra"
  writes after one of these candidates would produce a *predictable*,
  checkable byte pattern, then verify against file bytes).

## LICENSING, CREDITS & GITHUB SAFETY

Full writeup in `CREDITS_AND_LICENSING.md` (handed over alongside this
addendum) — summarized here for continuity with the rest of this
document.

**Third-party tools/resources used this session, none of which get
redistributed:**
- **Ghidra** (Apache-2.0, NSA) — only its `mfc42.exports` ordinal→name
  table was used, to resolve `MFC42.DLL` import ordinals in `MaxED.exe`.
- **CFR decompiler** (MIT, Lee Benfield) — used once to decompile
  `ldb-to-lvl.jar` for research.
- **`ldb-to-lvl.jar`**, a third-party LDB→LVL converter by an author
  going by **artkuznet** (from the Java package name). No bundled
  license, no upstream repo located. Decompiled and *read* to
  understand the `.lvl` per-polygon record layout; `lvl_reader.py` was
  then written independently and cross-checked byte-for-byte against
  real files, not transcribed from the Java. Credit line to use in any
  public repo: *"LVL polygon record layout cross-referenced against
  artkuznet's `ldb-to-lvl` converter (decompiled for research; no
  upstream repository found)."*
- **Wine** (LGPL-2.1) — hosted the person's own copy of `MaxED.exe` for
  live validation; the binary itself was never redistributed.
- **`pefile`** (MIT) / **`olefile`** (BSD) — standard Python libraries.

**Remedy/Rockstar material used for research — must NOT be committed to
a public repo, including in git history:** `MaxED.exe`/`MaxED.zip`,
`MaxED_Tutorial_v1_01HH-R1.chm`, `ai_test_01.doc`, and every `.lvl`/
`.ldb`/`.ai` file touched this session that contains real level
geometry or embedded textures (`ai_test_01.lvl`, `BasicRoom.lvl`,
`SUBWAY_A.LVL`, `Manor_Outside.lvl`, `recompiled.ldb`,
`exit_test_01_from_jar.lvl`, extracted textures like `BETON45.JPG`,
etc.). The person's own Python/Blender-addon source code, and prose
documentation of the file formats (this addendum included), are fine to
publish — file formats and the facts of their byte layout aren't
copyrightable; only someone else's specific code expression is.

**Not legal advice** — see `CREDITS_AND_LICENSING.md` §1 for that caveat
in full, and for `.gitignore`/history-scrubbing guidance if any of the
sensitive files above were ever committed locally before this note.

