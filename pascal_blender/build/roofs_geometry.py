"""Roof-segment mesh builder (spec docs/spec/03-roofs.md).

Builds each of the 7 roofTypes as a SOLID envelope whose outer silhouette
follows the spec's shell math: eaves extended by deckExt = WT/2 + OV*cos(θ)
in plan, eave underside dropped by ext*tan(θ) to stay coplanar with the
slope, ridge at WH + RH + (DT+ST)/cos(θ). The editor's hollow six-shell CSG
(wall/inner/deck/shingle sandwiches) is approximated by this solid — the
parametric truth lives in pascal_params/pascal_json, so a later exact builder
can regenerate without loss.

Coordinates are BLENDER segment-local: Pascal (x, y, z) -> (x, -z, y);
width spans X, depth spans Y, up is +Z. Face material indices follow the
editor's slot scheme: 0 wall/trim (verticals + soffits), 1 deck (fascia
band), 3 shingle (up-facing slopes). Slot 2 (interior) is unused while the
solid approximation stands.

Standalone on purpose: depends only on bpy/bmesh/math so it can be ported
or tested in isolation.
"""
from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

import bmesh
import bpy

DEFAULTS = {
    "roofType": "gable",
    "width": 8.0,
    "depth": 6.0,
    "wallHeight": 0.5,
    "roofHeight": 2.5,
    "wallThickness": 0.1,
    "deckThickness": 0.1,
    "overhang": 0.3,
    "shingleThickness": 0.05,
}

SHINGLE_NORMAL_EPS = 0.02  # editor: normal.y > 0.02 (Pascal) == normal.z here


def _param(params: dict, key: str) -> float:
    value = params.get(key)
    return DEFAULTS[key] if value is None else value


def build_roof_segment_mesh(params: dict, name: str) -> "bpy.types.Mesh":
    roof_type = str(_param(params, "roofType"))
    w = max(float(_param(params, "width")), 0.01)
    d = max(float(_param(params, "depth")), 0.01)
    wh = float(_param(params, "wallHeight"))
    rh = float(_param(params, "roofHeight"))
    wt = float(_param(params, "wallThickness"))
    dt = float(_param(params, "deckThickness"))
    ov = float(_param(params, "overhang"))
    st = float(_param(params, "shingleThickness"))

    if roof_type == "flat" or rh <= 0:
        verts, faces = _flat(w, d, wh, dt + st, wt, ov)
    elif roof_type == "gable":
        verts, faces = _gable(w, d, wh, rh, wt, dt, ov, st)
    elif roof_type == "shed":
        verts, faces = _shed(w, d, wh, rh, wt, dt, ov, st)
    elif roof_type == "hip":
        verts, faces = _hip(w, d, wh, rh, wt, dt, ov, st)
    elif roof_type == "gambrel":
        verts, faces = _gambrel(w, d, wh, rh, wt, dt, ov, st)
    elif roof_type == "mansard":
        verts, faces = _mansard(w, d, wh, rh, wt, dt, ov, st)
    elif roof_type == "dutch":
        verts, faces = _dutch(w, d, wh, rh, wt, dt, ov, st)
    else:
        verts, faces = _gable(w, d, wh, rh, wt, dt, ov, st)

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.validate()

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()

    _tag_materials(mesh)
    return mesh


def _slope(rh: float, run: float) -> Tuple[float, float, float]:
    """(tan, cos, sin) of the slope angle for rise rh over run."""
    if run <= 0 or rh <= 0:
        return 0.0, 1.0, 0.0
    theta = math.atan2(rh, run)
    return math.tan(theta), math.cos(theta), math.sin(theta)


def _prism_from_profile(
    profile: Sequence[Tuple[float, float]], half_w: float
) -> Tuple[List[Tuple[float, float, float]], List[Tuple[int, ...]]]:
    """Extrude a (y, z) cross-section profile along X to +/- half_w, capping
    the ends. The profile must be a simple CCW polygon."""
    n = len(profile)
    verts = [(-half_w, y, z) for y, z in profile] + [(half_w, y, z) for y, z in profile]
    faces: List[Tuple[int, ...]] = [
        tuple(range(n - 1, -1, -1)),          # -X cap
        tuple(range(n, 2 * n)),               # +X cap
    ]
    for i in range(n):
        j = (i + 1) % n
        faces.append((i, j, n + j, n + i))
    return verts, faces


def _gable_profile(d: float, wh: float, rh: float, ext: float, top: float) -> List[Tuple[float, float]]:
    """Cross-section (y, z) of a gable body: walls up to eaves, slopes to the
    ridge at z=top; eaves extended outward by ext and dropped to stay on the
    slope plane."""
    half_d = d / 2
    run = half_d
    tan_t = (top - wh) / run if run > 0 else 0.0
    eave_y = half_d + ext
    eave_z = wh - ext * tan_t
    base = min(0.0, eave_z - 0.05) if wh <= 0 else 0.0
    return [
        (-eave_y, base),
        (eave_y, base),
        (eave_y, eave_z),
        (0.0, top),
        (-eave_y, eave_z),
    ]


def _ridge_top(wh: float, rh: float, dt: float, st: float, cos_t: float) -> float:
    return wh + rh + (dt + st) / max(cos_t, 1e-6)


def _gable(w, d, wh, rh, wt, dt, ov, st):
    tan_t, cos_t, sin_t = _slope(rh, d / 2)
    ext = wt / 2 + ov * cos_t
    top = _ridge_top(wh, rh, dt, st, cos_t)
    profile = _gable_profile(d, wh, rh + (dt + st) / max(cos_t, 1e-6), ext, top)
    return _prism_from_profile(profile, w / 2 + wt / 2)


def _shed(w, d, wh, rh, wt, dt, ov, st):
    tan_t, cos_t, sin_t = _slope(rh, d)
    ext = wt / 2 + ov * cos_t
    thick = (dt + st) / max(cos_t, 1e-6)
    half_d = d / 2
    low_y, high_y = half_d + ext, -(half_d + ext)
    low_z = wh - ext * tan_t
    high_z = wh + rh + thick + ext * tan_t
    base = min(0.0, low_z - 0.05) if wh <= 0 else 0.0
    profile = [
        (high_y, base),
        (low_y, base),
        (low_y, low_z + thick),
        (high_y, high_z),
    ]
    return _prism_from_profile(profile, w / 2 + wt / 2)


def _hip(w, d, wh, rh, wt, dt, ov, st):
    tan_t, cos_t, _ = _slope(rh, min(w, d) / 2)
    ext = wt / 2 + ov * cos_t
    top = _ridge_top(wh, rh, dt, st, cos_t)
    hx, hy = w / 2 + ext, d / 2 + ext
    eave_z = wh - ext * tan_t
    base = min(0.0, eave_z - 0.05) if wh <= 0 else 0.0

    base_ring = [(-hx, -hy, base), (hx, -hy, base), (hx, hy, base), (-hx, hy, base)]
    eave_ring = [(-hx, -hy, eave_z), (hx, -hy, eave_z), (hx, hy, eave_z), (-hx, hy, eave_z)]

    # Ridge along the LONG axis; square footprint degenerates to a pyramid.
    run = min(w, d) / 2
    if w >= d:
        r0, r1 = (-(w / 2 - run), 0.0, top), ((w / 2 - run), 0.0, top)
    else:
        r0, r1 = (0.0, -(d / 2 - run), top), (0.0, (d / 2 - run), top)

    verts = base_ring + eave_ring + [r0, r1]
    faces = [
        (3, 2, 1, 0),                      # bottom
        (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7),  # walls
    ]
    if w >= d:
        faces += [(4, 5, 9, 8), (6, 7, 8, 9), (5, 6, 9), (7, 4, 8)]
    else:
        faces += [(5, 6, 9, 8), (7, 4, 8, 9), (4, 5, 8), (6, 7, 9)]
    return verts, faces


def _gambrel(w, d, wh, rh, wt, dt, ov, st):
    # Two pitches: lower steep (rise 0.6*RH over D/4), upper shallow.
    lower_rise = 0.6 * rh
    tan_l, cos_l, _ = _slope(lower_rise, d / 4)
    ext = wt / 2 + ov * cos_l
    thick = (dt + st) / max(cos_l, 1e-6)
    half_d = d / 2
    mid_y = d / 4
    eave_y = half_d + ext
    eave_z = wh - ext * tan_l
    mid_z = wh + lower_rise
    top = wh + rh + thick
    base = min(0.0, eave_z - 0.05) if wh <= 0 else 0.0
    profile = [
        (-eave_y, base),
        (eave_y, base),
        (eave_y, eave_z),
        (mid_y, mid_z + thick),
        (0.0, top),
        (-mid_y, mid_z + thick),
        (-eave_y, eave_z),
    ]
    return _prism_from_profile(profile, w / 2 + wt / 2)


def _mansard(w, d, wh, rh, wt, dt, ov, st):
    # Hip with two pitches: steep lower band, shallow top; inset 0.15*min(w,d).
    inset = 0.15 * min(w, d)
    lower_rise = 0.7 * rh
    tan_l, cos_l, _ = _slope(lower_rise, inset)
    ext = wt / 2 + ov * cos_l
    thick = (dt + st) / max(cos_l, 1e-6)
    hx, hy = w / 2 + ext, d / 2 + ext
    ix, iy = w / 2 - inset, d / 2 - inset
    if ix <= 0.005 or iy <= 0.005:
        return _hip(w, d, wh, rh, wt, dt, ov, st)
    eave_z = wh - ext * tan_l
    band_z = wh + lower_rise + thick
    top = wh + rh + thick
    base = min(0.0, eave_z - 0.05) if wh <= 0 else 0.0

    verts = [
        (-hx, -hy, base), (hx, -hy, base), (hx, hy, base), (-hx, hy, base),
        (-hx, -hy, eave_z), (hx, -hy, eave_z), (hx, hy, eave_z), (-hx, hy, eave_z),
        (-ix, -iy, band_z), (ix, -iy, band_z), (ix, iy, band_z), (-ix, iy, band_z),
    ]
    run2 = min(ix, iy)
    if ix >= iy:
        verts += [(-(ix - run2), 0.0, top), ((ix - run2), 0.0, top)]
    else:
        verts += [(0.0, -(iy - run2), top), (0.0, (iy - run2), top)]
    faces = [
        (3, 2, 1, 0),
        (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7),           # walls
        (4, 5, 9, 8), (5, 6, 10, 9), (6, 7, 11, 10), (7, 4, 8, 11),       # steep band
    ]
    if ix >= iy:
        faces += [(8, 9, 13, 12), (10, 11, 12, 13), (9, 10, 13), (11, 8, 12)]
    else:
        faces += [(9, 10, 13, 12), (11, 8, 12, 13), (8, 9, 12), (10, 11, 13)]
    return verts, faces


def _dutch(w, d, wh, rh, wt, dt, ov, st):
    # Hip lower part with a small gable poking out of the top.
    inset = 0.25 * min(w, d)
    tan_t, cos_t, _ = _slope(rh, min(w, d) / 2)
    ext = wt / 2 + ov * cos_t
    thick = (dt + st) / max(cos_t, 1e-6)
    hx, hy = w / 2 + ext, d / 2 + ext
    eave_z = wh - ext * tan_t
    base = min(0.0, eave_z - 0.05) if wh <= 0 else 0.0
    top = wh + rh + thick
    # Height where the hip transitions to the dutch gable.
    band_z = wh + rh * 0.55 + thick

    along_x = w >= d
    if along_x:
        gx, gy = w / 2 - inset, (d / 2) * (1 - 0.55)
    else:
        gx, gy = (w / 2) * (1 - 0.55), d / 2 - inset
    gx, gy = max(gx, 0.01), max(gy, 0.01)

    verts = [
        (-hx, -hy, base), (hx, -hy, base), (hx, hy, base), (-hx, hy, base),
        (-hx, -hy, eave_z), (hx, -hy, eave_z), (hx, hy, eave_z), (-hx, hy, eave_z),
        (-gx, -gy, band_z), (gx, -gy, band_z), (gx, gy, band_z), (-gx, gy, band_z),
    ]
    if along_x:
        verts += [(-gx, 0.0, top), (gx, 0.0, top)]
        ridge_faces = [
            (8, 9, 13, 12), (10, 11, 12, 13),      # top slopes
            (9, 10, 13), (11, 8, 12),              # dutch gable end walls
        ]
    else:
        verts += [(0.0, -gy, top), (0.0, gy, top)]
        ridge_faces = [
            (9, 10, 13, 12), (11, 8, 12, 13),
            (8, 9, 12), (10, 11, 13),
        ]
    faces = [
        (3, 2, 1, 0),
        (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7),
        (4, 5, 9, 8), (5, 6, 10, 9), (6, 7, 11, 10), (7, 4, 8, 11),
    ] + ridge_faces
    return verts, faces


def _flat(w, d, wh, thick, wt, ov):
    ext = wt / 2 + ov
    hx, hy = w / 2 + ext, d / 2 + ext
    top = wh + thick
    base = 0.0 if wh > 0 else -0.05
    verts = [
        (-hx, -hy, base), (hx, -hy, base), (hx, hy, base), (-hx, hy, base),
        (-hx, -hy, top), (hx, -hy, top), (hx, hy, top), (-hx, hy, top),
    ]
    faces = [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return verts, faces


def _tag_materials(mesh: "bpy.types.Mesh") -> None:
    """Editor slot scheme on the solid envelope: up-facing -> 3 shingle,
    down-facing -> 0 (soffit/trim), verticals -> 0 walls, except the fascia
    band (vertical faces whose centroid sits above the walls) -> 1 deck."""
    if not mesh.polygons:
        return
    max_z = max((v.co.z for v in mesh.vertices), default=0.0)
    for poly in mesh.polygons:
        n = poly.normal
        if n.z > SHINGLE_NORMAL_EPS:
            poly.material_index = 3
        elif n.z < -0.5:
            poly.material_index = 0
        else:
            # vertical: fascia if it's a thin band near the eave line
            poly.material_index = 1 if (max_z - poly.center.z) < 0.35 * max_z else 0
