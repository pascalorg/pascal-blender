# Releasing pascal-blender

## One-time setup (publishing to pascalorg)

```sh
cd ~/Documents/GitHub/pascal-blender
gh repo create pascalorg/pascal-blender --public \
    --description "Open Pascal editor scenes in Blender — drag & drop, fully editable, zero data loss" \
    --source . --push
```

(Requires admin rights on the pascalorg org; otherwise create the repo in the
GitHub UI and `git remote add origin git@github.com:pascalorg/pascal-blender.git && git push -u origin master:main`.)

Then in the repo settings: add topics `blender`, `blender-extension`,
`blender-addon`, `pascal`, `architecture`, `3d`; set the social preview image
to `docs/marketing/hero-clay-render.png`.

## Every release

1. Bump `version` in `pascal_blender/blender_manifest.toml`.
2. Commit, then tag and push:

   ```sh
   git tag v0.1.0 && git push origin v0.1.0
   ```

3. The `release.yml` workflow runs the full headless test suite, builds the
   extension zip with Blender itself, and attaches it to a GitHub Release
   with auto-generated notes. Users install by dragging that zip into Blender.

CI (`ci.yml`) runs the same suite on every push/PR, so a red main never ships.

## Distribution channels (in order of reach)

1. **GitHub Releases** (live from day one — the zip installs by drag & drop).
2. **extensions.blender.org** — the official marketplace; users install from
   inside Blender with one click and get auto-updates. Submit the built zip
   at https://extensions.blender.org/submit/ (needs a Blender ID; review
   typically takes days). The manifest already meets the requirements
   (Extensions format, SPDX license, network/files permissions declared).
   Recommended once v0.1 has survived a week of public issues.
3. **Pascal editor cross-link** — a small PR to pascalorg/editor adding a
   "Download scene (.json)" button with a "Open in Blender" hint linking to
   this repo. Highest-intent audience of all.
