# pascal-blender

**Import [Pascal editor](https://editor.pascal.app) scenes into Blender — clean project, zero information loss.**

The Pascal editor stores everything as a parametric JSON scene graph (walls, slabs,
roofs, doors, windows, furniture). Its built-in GLB export bakes that into a triangle
soup. This extension consumes the **scene JSON instead** and rebuilds the model as
native, editable Blender data — while keeping every byte of the source recoverable
from the `.blend` alone.

## What you get

- **Native rebuild** — walls (mitered corners, live boolean openings), slabs
  (with holes and the editor's 0.05 m outset), ceilings, the 7 parametric roof
  types, parametric doors/windows, furniture as instanced collections, lights,
  saved cameras, zones, site boundary.
- **Clean project** — meters, Z-up, `Site → Building → Level` as nested
  collections, one level-origin Empty per storey (grab it to slide a whole
  floor), deduplicated `Pascal/*` materials, human-readable names.
- **Zero information loss** — the full original JSON lives in the
  `pascal_source.json` Text datablock, and every node carries its verbatim JSON
  in a `pascal_json` custom property. The test suite proves a byte-faithful
  reconstruction (deep-equal) of the source file from the `.blend` alone —
  including unknown node types, unknown fields, orphan nodes, and legacy
  (pre-migration) shapes.
- **Editable openings** — doors/windows are objects parented to their wall;
  each drags a hidden cutter box, so moving a door moves its hole live.

## Install

Requires **Blender 4.2+** (developed and tested on 4.5 LTS).

1. Download the latest `pascal_blender-x.y.z.zip` from
   [Releases](https://github.com/pascalorg/pascal-blender/releases).
2. In Blender: **drag the zip into the Blender window** and click *Install* —
   or Edit → Preferences → Get Extensions → ⌄ (top-right) → *Install from Disk…*.
3. `File → Import → Pascal Scene (.json)` — or drag a `.json` scene straight
   into the 3D viewport.

To build the zip from source:

```sh
/Applications/Blender.app/Contents/MacOS/Blender --command extension build \
    --source-dir pascal_blender --output-dir dist
```

## Getting the JSON out of the Pascal editor

The editor autosaves the scene graph to `localStorage` under the key
`pascal-editor-scene`. Until a "Download scene JSON" button ships in the editor
(a few-line PR — the export panel already exists), run this in the browser
console on editor.pascal.app:

```js
const s = localStorage.getItem('pascal-editor-scene');
const a = document.createElement('a');
a.href = URL.createObjectURL(new Blob([s], {type: 'application/json'}));
a.download = 'scene.pascal.json';
a.click();
```

## Import options

| Option | Default | Meaning |
|---|---|---|
| Download assets | on | fetch furniture GLBs (cached under the extension's user dir); off = labeled wire placeholders |
| Bake openings | off | apply wall booleans instead of keeping live modifiers |
| Physical glass | off | add Transmission/IOR for real refraction in Cycles |
| Apply texture field | on | honor `material.texture` (declared but unused by the editor) |
| Item materials | Editor look | or keep each GLB's original materials |
| Asset CDN | editor.pascal.app | base URL for relative asset paths |
| Watts per light unit | 60 | calibration for the editor's unitless light intensities |

## The data layer (how losslessness works)

| Where | What |
|---|---|
| Text datablock `pascal_source.json` | the complete original file, pretty-printed, fake-user'd |
| Scene props `pascal_source_hash`, `pascal_root_node_ids`, … | sha256 of the original bytes, root order, unknown top-level keys |
| Every anchor datablock: `pascal_id`, `pascal_type`, `pascal_json` | node identity + its verbatim pre-migration JSON (string prop) |
| `pascal_params` | human-readable mirror of the geometry-driving fields |
| `pascal_migrated` / `pascal_synthetic` | flags for legacy-roof migration artifacts |

Identity lives **only** in `pascal_id` — never in object names (Blender truncates
and suffixes those). An exporter (roadmap) reads `pascal_json`, overlays actual
Blender transforms/params, and re-emits `{nodes, rootNodeIds}`.

## Development

```sh
# pure-python core tests (no Blender needed)
python3 tests/test_openings.py
python3 tests/test_wallnet.py

# full headless suite: import every fixture, prove losslessness, save/reload
/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup \
    --python tests/run_headless.py
```

See `TESTING.md` for the full matrix and `docs/design/lossless-mapping.md` for
the design (field-by-field disposition tables for all 14 node types).
`docs/spec/` holds reimplementation-grade extractions of the editor's geometry
algorithms (wall miters, the 7 roof types, parametric openings, materials,
level stacking) — the importer is built against those, not against guesses.

## Known limitations / roadmap

- `asset://…` URLs (images/GLBs stored in the authoring browser's IndexedDB)
  are unrecoverable from the JSON; you get placeholders + the URL in props.
- Exporter (Blender → Pascal JSON round-trip after edits) is designed
  (`docs/design/lossless-mapping.md` §7) but not yet implemented; the
  proto-exporter in `pascal_blender/testing/checks.py` already proves the
  no-edit round-trip.
- Merged-roof CSG (segments carving into each other) is approximated: segments
  are built individually, not union-merged.
- Wall `frontSide`/`backSide` visual overrides and zone hover effects are
  editor view-state and deliberately not imported (data preserved).

MIT — see LICENSE. Not affiliated with Pascal; the editor itself is MIT
([pascalorg/editor](https://github.com/pascalorg/editor)).
