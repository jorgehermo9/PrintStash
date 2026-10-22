# ADR-0018: Produce other formats as Renditions, never as a second source of truth

Status: Proposed — design for #536; PLY sources tracked in #537.

## Context

A Model can already hold several Artifacts in different formats: an author's
STEP, 3MF and STL sit side by side as independent uploads. What PrintStash
cannot do is give a user a format they did not upload. The next tool in a
printing workflow often needs one: Bambu Studio loads only 3MF from a URL,
mesh-only tools need STL or OBJ, and PLY scans open in few slicers (#537).

Conversion is directional and usually lossy. STEP → mesh approximates exact
surfaces; 3MF → STL drops objects, colours, plates and settings; mesh → STEP
produces a faceted solid that looks editable and is not. The design has to make
that fidelity visible and keep the converted file from being mistaken for an
upload.

## Decision

1. **A Rendition is a derivative.** A Rendition is a file in another format
   produced from one Artifact by a versioned conversion recipe. It is a
   derivative kind per target format (`rendition_stl`, `rendition_3mf`,
   `rendition_obj`) in `artifact_derivatives`, produced by a Job Definition on
   `derive.native`. It is not a `File` row. `viewer_stl` stays a separate,
   internal kind: its contract follows viewer needs (ADR-0014), a Rendition's
   follows the downloaded file.
2. **Uploads stay first-class and win.** A Model's uploaded Artifact in the
   requested format is always offered instead of a conversion. There is no
   override mechanism, so no file is ever both derived and authoritative.
3. **One mechanism, three policies.** An administrator sets one policy per
   target format: `always` (produced in the background and never evicted),
   `on_demand` (the default: produced on first request, evicted after a period
   without use or when over a size budget, recreated transparently) or `off`.
   The policy decides only when a Rendition is produced and how long it is kept,
   never what it is.
4. **Keep as Artifact.** A user can promote a Rendition to an Artifact through
   `persist_artifact`, with provenance naming the source Artifact and recipe.
   The result is an ordinary upload with the full Artifact lifecycle and is no
   longer tied to the source.
5. **Fidelity is a closed classification.** Every matrix cell is `exact`,
   `attributes_dropped`, `approximated` or `refused`. The Rendition records its
   class, what it dropped and any tolerance, and the UI shows them.
6. **Refuse instead of fabricating.** Conversions with no faithful result are not
   offered: mesh → STEP, DXF → anything, mesh ↔ G-code (slicing is not
   conversion). Requests for them fail validation; they never enqueue work.

## Conversion matrix

Rows are sources and columns are targets. "—" means same format: nothing to do.

| Source ↓ / Target → | STL | 3MF | OBJ | STEP |
|---|---|---|---|---|
| **STL** | — | exact¹ | exact | refused |
| **3MF** | attributes dropped² | — | attributes dropped² | refused |
| **OBJ** | attributes dropped³ | attributes dropped³ | — | refused |
| **STEP** | approximated⁴ | approximated⁴ | approximated⁴ | — |
| **PLY** (#537) | attributes dropped⁵ | attributes dropped⁵ | attributes dropped⁵ | refused |
| **DXF** | refused⁶ | refused⁶ | refused⁶ | refused⁶ |
| **G-code / bgcode** | refused⁷ | refused⁷ | refused⁷ | refused⁷ |

1. STL declares no units. The 3MF states millimetres, the same assumption
   PrintStash already makes for STL metadata. Coordinates are copied from
   float32 without loss.
2. Build items and components are flattened into world coordinates and merged
   into one mesh. Object names survive as OBJ groups. Colours, paint, materials,
   modifiers, plates, print settings and embedded thumbnails are dropped and
   listed. 3MF coordinates rounded to STL float32 are reported as precision loss.
3. Polygons are triangulated. Non-planar faces with more than three vertices are
   ambiguous, so the Rendition is reported as `approximated` when it has any.
   UVs, normals and MTL materials are dropped. Groups become 3MF objects.
4. Tessellated by the existing STEP reader (OCP) at its recorded linear and
   angular deflection, converted to millimetres from the units the STEP file
   declares. Metadata, preview and Rendition therefore describe the same mesh.
   Assemblies become named 3MF objects; for STL they are merged.
5. Faces are kept and vertex colours dropped. A face-less PLY (point cloud) is
   rejected at ingestion and never reaches this matrix.
6. A 2D drawing needs an extrusion the user has to choose; that is modelling,
   not conversion.
7. Mesh → G-code is slicing and needs printer, filament and profile choices.
   G-code → mesh is not recoverable. Lossless bgcode ↔ gcode repackaging is a
   possible later row, in its own class.

PLY is not a target: no current printing workflow needs it.

Writers must run on every supported platform. lib3mf is installed only on
x86_64, so the 3MF writer must not depend on it.

## Choosing a source

A Model can hold several uploaded Artifacts, and the source changes what a
Rendition can carry. A request on one Artifact converts that Artifact. A
Model-level request, Open in slicer and the `always` policy choose one
**conversion source** per Model and target:

1. **An upload in the target format wins.** Use the Model's live uploaded
   Artifact in that format and convert nothing.
2. **A pinned source wins next.** A user can pin one of the Model's mesh
   Artifacts as its conversion source. This is for the case the ranking cannot
   see, for example an author's print-ready 3MF whose orientation matters more
   than the STEP's exactness. The pin is cleared when that Artifact is trashed.
3. **Otherwise use the source that carries the most information.** Rank formats
   by what they hold, not by the fidelity class of one conversion step: STEP
   (exact surfaces, declared units), then 3MF (declared units, objects and
   placements), then OBJ, then PLY, then STL (no units, one shell).
4. **Within a format, use the latest live version.** Ties at the same version go
   to the most recently uploaded Artifact.

The choice is computed whenever it is needed, never stored as a separate
decision. Each Rendition belongs to one source Artifact version, because a
derivative row is keyed by `file_id`, so the Rendition itself records which
source it came from. Users can still convert any other Artifact on request.

### When the source changes

Uploading, trashing, restoring or pinning can change the conversion source. A
Rendition made from an Artifact that is no longer the conversion source is
**superseded**:

- Model-level requests and Open in slicer stop serving it straight away.
- Under `always`, the source query finds the Model missing its Rendition from
  the new source and produces one at interactive priority. The superseded
  Rendition keeps being shown until the new one is ready, as other derivative
  outputs already are.
- Under `on_demand`, the next request produces it from the new source.
- The superseded Rendition can be evicted at once, whatever its last access.
  Until then it still serves explicit requests on its own Artifact.

An upload in the target format supersedes every Rendition of that target on the
Model (rule 1), so the user's own file always replaces a converted one.

## Attribution

A Rendition names the **specific Artifact** it was made from, not only its
format. A Model can hold two STEP files, or several versions of one, so the
format alone is ambiguous. The derivative record and the UI carry:

- the source's original filename, version and SHA-256, plus a link to that
  Artifact;
- the source format, the fidelity class, what was dropped, and the tolerance
  where there is one;
- the recipe identity, and why this source was chosen (`target_upload`,
  `pinned`, `ranked`, `requested`).

The UI shows this as "Converted from `bracket.step` · v3 · approximated at
0.05 mm". The downloaded file is named after the source (`bracket.step` →
`bracket.3mf`), and the file records where it came from wherever the format
allows:

- **3MF:** core-specification metadata. `Application` names PrintStash. `Title`,
  `Designer`, `Description`, `Copyright` and `LicenseTerms` come from the Model
  and its import provenance when known, so a scan or download from a model site
  keeps its author and licence when shared.
- **OBJ:** a leading comment block with the same facts.
- **STL:** the 80-byte header carries a short source note. It must never begin
  with `solid`, which marks ASCII STL and would break readers, including
  PrintStash's own density estimator.

Provenance is copied, never invented. A field the Model does not record is
left out rather than filled with a placeholder.

## Integration

| Subsystem | Behaviour |
|---|---|
| Model identity, ingestion dedup | A Rendition never contributes. Its SHA-256 goes into a durable ledger that eviction does not touch. Re-uploading a downloaded Rendition attaches it to its source Model as an Artifact instead of creating a new Model. |
| Versions, new uploads | A Rendition belongs to one Artifact version. A new version, or a new Artifact that becomes the conversion source, supersedes it (see "When the source changes"). |
| Recipe bump | The derivative source finds Renditions missing at the new recipe. `always` targets are reconverted at backfill priority, `on_demand` ones on their next request. |
| Trash | A Rendition follows its source Artifact or Model into and out of the trash and is deleted with it. |
| Backups, library transfer | Excluded, because they can be regenerated. Restore brings them back through the policy. |
| GC, storage ownership | Rendition bytes use ownership intents like every derivative output. Eviction deletes only keys it owns and never walks storage. |
| Similarity, AI search | Excluded. A Rendition would match its own source and adds no text. |
| Revisions | Not applicable. A Rendition is never G-code. |
| Metadata, thumbnails | Not derived again. A Rendition shows its source's metadata and preview. |
| Downloads | Served through the existing download contract: current access check, `private` caching, its own representation validator, and API delivery as for generated previews. |
| Open in slicer | When the slicer cannot open the source format, the handoff serves a Rendition in a format it can (Bambu Studio → 3MF). A Rendition that is still pending shows as pending, not as an error. |
| Printers | Unchanged. Printers receive only G-code. |
| Public shares | Serve the original only, at first. |
| Vault stats | Report a separate "Converted files" size, so cached bytes are not counted as library growth. |

## Alternatives considered

- **Rendition as a `File` row with an `origin` column.** Rejected. Every
  Artifact consumer listed above would need an origin filter. One missing filter
  creates a duplicate Model, a self-match or a backup full of regenerable bytes.
- **One-time export behind an expiring link.** Rejected as the main mechanism.
  It repeats expensive conversions on every download, and an expiring link only
  helps sharing outside PrintStash. That belongs to a share-link feature.
- **Pre-generating every format.** Rejected as the default. It roughly doubles
  stored bytes and turns every recipe bump into a library-wide conversion. It
  remains available per target as the `always` policy.
- **Uploads overriding derivatives.** Not needed once resolution prefers
  uploads (decision 2).

## Consequences

- `CONTEXT.md` gains **Rendition** when this is implemented (avoid: export, which
  already means library export; derived Artifact; copy).
- A new demand record per Artifact and target holds the request time and last
  access, which on-demand production and eviction need.
- A setting holds the per-target policy and eviction budget.
- A Model gains an optional pinned conversion source, an Artifact of that Model.
- Conversion is bounded by the same native admission and mesh-density limits as
  metadata. A source over the ceiling is refused with `geometry_work_limit`, so
  the densest meshes cannot be converted until those limits change.
- Each matrix cell has its own recipe, so a change to one writer reconverts only
  the Renditions of that cell.
