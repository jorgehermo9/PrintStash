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

A request on one Artifact converts that Artifact. A Model-level request, and the
`always` policy, choose one source per Model and target. The rule is:

1. If the Model has a live uploaded Artifact in the target format, use that and
   convert nothing.
2. Otherwise choose the most exact source by fidelity class. Ties go to STEP,
   then 3MF, then OBJ, then PLY, then STL.
3. Within a format, use the latest live version.

The Rendition records which source was chosen. Users can still convert any other
Artifact on request.

## Integration

| Subsystem | Behaviour |
|---|---|
| Model identity, ingestion dedup | A Rendition never contributes. Its SHA-256 goes into a durable ledger that eviction does not touch. Re-uploading a downloaded Rendition attaches it to its source Model as an Artifact instead of creating a new Model. |
| Versions | A Rendition belongs to one Artifact version. A new version gets its own; the old one stays valid for the old version. |
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
- Conversion is bounded by the same native admission and mesh-density limits as
  metadata. A source over the ceiling is refused with `geometry_work_limit`, so
  the densest meshes cannot be converted until those limits change.
- Each matrix cell has its own recipe, so a change to one writer reconverts only
  the Renditions of that cell.
