"""fitprobe — typed geometry queries for enclosure work.

Why this exists: across the OpenClaw / lobster-box builds the same handful of
checks got hand-written again and again (clash volume, "does the module fit",
"where is the wall at this height", "is this one solid"), each time slightly
differently, and twice with a bug that made the answer meaningless. Adam's
Fusion add-in solves the same problem by exposing a fixed set of typed read
queries instead of letting the model improvise a script per question; this is
that idea for build123d.

Rules of use:
  - every query returns plain numbers/dicts, never a shape, so results are
    cheap to print and safe to put in a report;
  - a query that cannot answer says which argument is missing, in the style of
    Adam's "'area' query requires entityToken" - never returns 0.0 silently
    (a degenerate boolean returning empty is the classic false PASS);
  - measurement is separate from judgement: callers decide what a number means.

    from fitprobe import (
        PLUG_ENVELOPE, clash, fits, envelope, wall_at, solids, outside, sag,
        plug_sweep, slide_path, fpc_tail_length,
        assert_healthy, cylinder_axes, match_holes, under_board_top_z, floating_stop_check,
    )
"""

from __future__ import annotations

import math
from build123d import Align, Axis, Box, Compound, GeomType, Location, Pos, Solid

# pcb-enclosure-fit §2 默认线缆插头胶壳包络（宽 × 高，mm）
PLUG_ENVELOPE: dict[str, tuple[float, float]] = {
    "USB-C": (13.0, 7.5),
    "micro-HDMI": (11.0, 7.0),
    "USB-A": (17.0, 8.5),
}


def _vol(shape) -> float:
    if shape is None:
        return 0.0
    sols = shape.solids()
    return sum(s.volume for s in sols) if sols else 0.0


def _need(cond, msg):
    if not cond:
        raise ValueError(f"fitprobe: {msg}")


def clash(a, b, detail: bool = False):
    """Interpenetration volume between two parts (mm^3).

    detail=True also returns each overlapping lump's bbox, which is what
    actually tells you *which* feature collides - a bare total sends you
    guessing. Returns float, or (float, [lumps]) with detail.
    """
    _need(a is not None and b is not None, "clash needs two shapes")
    ov = a & b
    total = _vol(ov)
    if not detail:
        return total
    lumps = []
    for s in sorted((ov.solids() if ov else []), key=lambda s: -s.volume):
        bb = s.bounding_box()
        lumps.append({
            "vol": round(s.volume, 1),
            "x": (round(bb.min.X, 1), round(bb.max.X, 1)),
            "y": (round(bb.min.Y, 1), round(bb.max.Y, 1)),
            "z": (round(bb.min.Z, 1), round(bb.max.Z, 1)),
        })
    return total, lumps


def fits(parts, w, d, h, center, tol: float = 0.05):
    """Does a w x d x h block centred at `center` clear every part?

    The honest way to ask "will the real module go in": a box stand-in tested
    against the actual shells, not a guess from the nominal cavity size.
    """
    _need(len(center) == 3, "fits needs center=(x, y, z)")
    block = Pos(*center) * Box(float(w), float(d), float(h))
    total = sum(clash(p, block) for p in parts)
    return {"clear": total < tol, "clash": round(total, 2)}


def envelope(parts, w, d, h, x=0.0, y_range=(), z_range=(), tol: float = 0.05):
    """Sweep a block's y (setback) and z (height) for clear placements.

    Returns every clear (y, z) plus the best-effort minimum clash when none
    are clear - "nothing fits" with the closest miss is far more useful than
    a bare failure.
    """
    _need(y_range and z_range, "envelope needs y_range and z_range")
    clear, best = [], None
    for y in y_range:
        for z in z_range:
            r = fits(parts, w, d, h, (x, y, z), tol)
            if r["clear"]:
                clear.append({"y": y, "z": z})
            elif best is None or r["clash"] < best["clash"]:
                best = {"y": y, "z": z, "clash": r["clash"]}
    return {"clear": clear, "closest_miss": None if clear else best}


def wall_at(body, z, thickness=0.4):
    """Where the wall actually is at height z: half-width and front/back face.

    On a tapered or crowned body every attachment (screw boss, shoulder disc,
    bracket) must be placed from this, not from the nominal half-width - the
    flank moves several mm over the height and features silently bury
    themselves in the wall.
    """
    _need(body is not None, "wall_at needs a body")
    sl = body & (Pos(0, 0, float(z)) * Box(1000, 1000, thickness))
    _need(_vol(sl) > 0, f"wall_at: no material at z={z}")
    bb = sl.bounding_box()
    return {"half_width": round(bb.max.X, 2), "front_y": round(bb.max.Y, 2),
            "back_y": round(bb.min.Y, 2)}


def solids(part, name=""):
    """One printed part must be ONE solid.

    Disjoint lumps each validate fine and still export a non-watertight mesh
    or print as unsupported islands; this is the check that catches a boss
    floating in the cavity or a plug ring that never touched its parent.
    """
    sols = part.solids()
    frag = []
    for s in sorted(sols, key=lambda s: -s.volume)[1:]:
        bb = s.bounding_box()
        frag.append({"vol": round(s.volume, 1),
                     "x": (round(bb.min.X, 1), round(bb.max.X, 1)),
                     "y": (round(bb.min.Y, 1), round(bb.max.Y, 1)),
                     "z": (round(bb.min.Z, 1), round(bb.max.Z, 1))})
    return {"name": name, "count": len(sols), "single": len(sols) == 1,
            "valid": part.is_valid, "fragments": frag}


def outside(part, envelope_body, tol: float = 0.05):
    """Material of `part` sticking out through the outer surface.

    Ribs, bosses and pads that breach the shell are invisible to an assembly
    interference check, because they belong to the same part as the wall.
    """
    _need(envelope_body is not None, "outside needs the outer body")
    out = part - envelope_body
    v = _vol(out)
    lumps = []
    for lump in sorted((out.solids() if out else []), key=lambda x: -x.volume)[:6]:
        bb = lump.bounding_box()
        lumps.append({"vol": round(lump.volume, 1),
                      "x": (round(bb.min.X, 1), round(bb.max.X, 1)),
                      "y": (round(bb.min.Y, 1), round(bb.max.Y, 1)),
                      "z": (round(bb.min.Z, 1), round(bb.max.Z, 1))})
    # Deliberate protrusions (shoulder discs, feet, bosses meant to stand
    # proud) land here too - read the lumps and judge, do not treat any
    # nonzero volume as a fault.
    return {"breaches": v > tol, "vol": round(v, 1), "lumps": lumps}


def sag(body, z, x_half, thickness=0.4):
    """How far a face bows back between its centre and +-x_half, at height z.

    A flat part (screen glass, a PCB, a lens) mounted on a crowned face needs
    this under ~0.5 mm or the bezel gaps. Measures the real surface rather
    than trusting the section formula.
    """
    sl = body & (Pos(0, 0, float(z)) * Box(1000, 1000, thickness))
    _need(_vol(sl) > 0, f"sag: no material at z={z}")
    y_mid = sl.bounding_box().max.Y
    edge = sl & (Pos(float(x_half), 0, float(z)) * Box(0.4, 1000, thickness))
    _need(_vol(edge) > 0, f"sag: face does not reach x={x_half} at z={z}")
    return round(y_mid - edge.bounding_box().max.Y, 2)


def plug_sweep(
    parts,
    axis: str,
    start: float,
    end: float,
    center_orth: tuple[float, float],
    plug_wh: str | tuple[float, float],
    opening_orth: tuple[float, float, float, float] | None = None,
    min_clearance: float = 0.0,
    tol: float = 0.01,
):
    """Sweep a cable plug overmould along `axis` from `start` (outside shell) to `end` (receptacle face).

    Catches openings sized only for the PCB receptacle shell where the cable overmould
    hits the outer wall or internal ribs before seating (pcb-enclosure-fit §2).
    """
    _need(axis.lower() in ("x", "y", "z"), "plug_sweep: axis must be 'x', 'y', or 'z'")
    _need(len(center_orth) == 2, "plug_sweep: center_orth needs (u, v)")
    _need(start != end, "plug_sweep: start and end must differ")
    if isinstance(plug_wh, str):
        _need(plug_wh in PLUG_ENVELOPE, f"plug_sweep: unknown plug key '{plug_wh}'")
        w, h = PLUG_ENVELOPE[plug_wh]
    else:
        _need(len(plug_wh) == 2 and plug_wh[0] > 0 and plug_wh[1] > 0, "plug_sweep: plug_wh needs (w, h) > 0")
        w, h = float(plug_wh[0]), float(plug_wh[1])

    lo_a, hi_a = (float(start), float(end)) if start < end else (float(end), float(start))
    u, v = float(center_orth[0]), float(center_orth[1])
    u0, u1 = u - w / 2.0, u + w / 2.0
    v0, v1 = v - h / 2.0, v + h / 2.0

    ax = axis.lower()
    if ax == "x":
        lo, sz = (lo_a, u0, v0), (hi_a - lo_a, w, h)
    elif ax == "y":
        lo, sz = (u0, lo_a, v0), (w, hi_a - lo_a, h)
    else:
        lo, sz = (u0, v0, lo_a), (w, h, hi_a - lo_a)

    sweep_box = Location(lo) * Box(*sz, align=(Align.MIN, Align.MIN, Align.MIN))
    named = parts.items() if isinstance(parts, dict) else [(f"part_{i}", p) for i, p in enumerate(parts)]
    _need(bool(named), "plug_sweep: parts must not be empty")
    clashes = {k: round(clash(p, sweep_box), 4) for k, p in named}
    clash_ok = all(val <= tol for val in clashes.values())

    margins = None
    min_margin = None
    margin_ok = True
    if opening_orth is not None:
        _need(len(opening_orth) == 4, "plug_sweep: opening_orth needs (ou0, ou1, ov0, ov1)")
        ou0, ou1, ov0, ov1 = (float(x) for x in opening_orth)
        margins = [round(u0 - ou0, 3), round(ou1 - u1, 3), round(v0 - ov0, 3), round(ov1 - v1, 3)]
        min_margin = min(margins)
        margin_ok = min_margin >= min_clearance - 1e-6

    return {
        "clear": clash_ok and margin_ok,
        "clashes": clashes,
        "margins": margins,
        "min_margin": min_margin,
    }


def _translate_solids(shape, delta: tuple[float, float, float]) -> list[Solid]:
    """Translate each solid leaf individually to avoid build123d Compound transform pitfalls."""
    sols = shape.solids() if hasattr(shape, "solids") else [shape]
    _need(bool(sols), "slide_path: shape has no solids")
    loc = Pos(*delta)
    return [loc * s for s in sols]


def slide_path(shell, unit, axis: str, stations, tol: float = 1.0):
    """Sweep `unit` along `axis` through `stations` offsets and check clash against `shell` at each step.

    Catches ribs or overhangs that block insertion mid-way even when the final seated pose (offset 0) is clear.
    """
    _need(shell is not None and unit is not None, "slide_path: needs shell and unit")
    _need(axis.lower() in ("x", "y", "z"), "slide_path: axis must be 'x', 'y', or 'z'")
    st_list = [float(s) for s in stations]
    _need(bool(st_list), "slide_path: stations must be non-empty")

    shell_sols = shell.solids() if hasattr(shell, "solids") else [shell]
    _need(bool(shell_sols), "slide_path: shell has no solids")

    ax = axis.lower()
    rows = []
    for off in st_list:
        delta = (off, 0.0, 0.0) if ax == "x" else ((0.0, off, 0.0) if ax == "y" else (0.0, 0.0, off))
        moved = _translate_solids(unit, delta)
        v = 0.0
        for ms in moved:
            for ss in shell_sols:
                v += clash(ms, ss)
        rows.append({"offset": round(off, 2), "clash": round(v, 3), "clear": v < tol})

    max_c = max(r["clash"] for r in rows)
    return {
        "clear": all(r["clear"] for r in rows),
        "max_clash": max_c,
        "stations": rows,
    }


def fpc_tail_length(span: float, dz: float, bend_r: float, extra_flat: float = 0.0):
    """Closed-curve length for a 180-deg U-folded FPC tail vs naive scalar estimate (pcb-enclosure-fit §12)."""
    _need(bend_r > 0 and span >= 0, "fpc_tail_length: needs bend_r > 0 and span >= 0")
    arc = math.pi * float(bend_r)
    rem_z = max(0.0, abs(float(dz)) - 2.0 * float(bend_r))
    straight = math.hypot(float(span), rem_z) + float(extra_flat)
    curve_mm = arc + straight
    scalar_mm = float(span) + abs(float(dz)) + float(extra_flat)
    return {
        "curve_mm": round(curve_mm, 3),
        "scalar_mm": round(scalar_mm, 3),
        "bend_arc_mm": round(arc, 3),
        "outer_od_mm": round(2.0 * float(bend_r), 3),
    }


def assert_healthy(part, min_vol: float, max_vol: float, name: str = "part", require_single: bool = True):
    """Guard against degenerate boolean remnants before running clash/fit checks (pcb-enclosure-fit §6.6)."""
    _need(part is not None, f"assert_healthy({name}): part is None")
    _need(min_vol > 0 and max_vol >= min_vol, f"assert_healthy({name}): invalid volume bounds [{min_vol}, {max_vol}]")
    sols = part.solids() if hasattr(part, "solids") else [part]
    vol = sum(s.volume for s in sols) if sols else 0.0
    valid = bool(getattr(part, "is_valid", True))
    _need(valid, f"assert_healthy({name}): shape is not valid")
    _need(min_vol <= vol <= max_vol, f"assert_healthy({name}): volume {vol:.2f} mm^3 outside [{min_vol}, {max_vol}]")
    if require_single:
        _need(len(sols) == 1, f"assert_healthy({name}): expected 1 solid, got {len(sols)}")
    return {"name": name, "volume_mm3": round(vol, 2), "solids": len(sols), "valid": valid}


def cylinder_axes(part, radius: float, tol: float = 0.05) -> dict[tuple[float, float], list[float]]:
    """Extract true cylinder axis (x, y) -> [z_min, z_max] via BRepAdaptor_Surface (pcb-enclosure-fit §6.8).

    Never use Face.center() on periodic cylindrical faces: it returns the parametric midpoint on the surface,
    shifted by a full radius `r` from the true hole/boss center.
    """
    _need(part is not None and radius > 0, "cylinder_axes: needs part and radius > 0")
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    out: dict[tuple[float, float], list[float]] = {}
    for f in part.faces().filter_by(GeomType.CYLINDER):
        if abs(f.radius - radius) <= tol:
            ax = BRepAdaptor_Surface(f.wrapped).Cylinder().Axis().Location()
            bb = f.bounding_box()
            key = (round(ax.X(), 3), round(ax.Y(), 3))
            span = out.setdefault(key, [ round(bb.min.Z, 3), round(bb.max.Z, 3) ])
            span[0] = min(span[0], round(bb.min.Z, 3))
            span[1] = max(span[1], round(bb.max.Z, 3))
    _need(bool(out), f"cylinder_axes: no cylinder face found with radius={radius} +/- {tol}")
    return out


def match_holes(
    expected_xy: list[tuple[float, float]],
    part,
    radius: float,
    radius_tol: float = 0.05,
    pos_tol: float = 0.05,
):
    """Cross-check expected PCB hole (x, y) coords against STEP cylinder axes (pcb-enclosure-fit §6.8)."""
    _need(bool(expected_xy), "match_holes: expected_xy must not be empty")
    found = cylinder_axes(part, radius, tol=radius_tol)
    rows = []
    for ex, ey in expected_xy:
        (px, py), z_span = min(found.items(), key=lambda kv: (kv[0][0] - ex) ** 2 + (kv[0][1] - ey) ** 2)
        d = math.hypot(px - ex, py - ey)
        rows.append({
            "expected": (round(ex, 3), round(ey, 3)),
            "actual": (px, py),
            "z_span": z_span,
            "delta_mm": round(d, 4),
            "ok": d <= pos_tol,
        })
    max_d = max(r["delta_mm"] for r in rows)
    count_ok = len(found) == len(expected_xy)
    return {
        "clear": all(r["ok"] for r in rows) and count_ok,
        "expected_count": len(expected_xy),
        "found_count": len(found),
        "max_delta_mm": max_d,
        "holes": rows,
    }


def under_board_top_z(
    board_xy_bounds: tuple[float, float, float, float],
    z_range: tuple[float, float],
    under_parts,
) -> dict:
    """Tall-column intersection to find the highest obstacle beneath a board (height-stack.md §2).

    Intersecting only the thin PCB slab (e.g. z=1.0..1.8) clips anything taller at 1.8 mm and misses
    posts protruding to z=3.2 mm. A full-height column over z_range catches the true top Z.
    """
    _need(len(board_xy_bounds) == 4, "under_board_top_z: board_xy_bounds needs (x0, y0, x1, y1)")
    _need(len(z_range) == 2 and z_range[1] > z_range[0], "under_board_top_z: z_range needs (z_min, z_max)")
    x0, y0, x1, y1 = (float(v) for v in board_xy_bounds)
    z0, z1 = float(z_range[0]), float(z_range[1])
    _need(x1 > x0 and y1 > y0, "under_board_top_z: degenerate XY bounds")
    col = Location((x0, y0, z0)) * Box(x1 - x0, y1 - y0, z1 - z0, align=(Align.MIN, Align.MIN, Align.MIN))
    parts_list = list(under_parts.values()) if isinstance(under_parts, dict) else list(under_parts)
    _need(bool(parts_list), "under_board_top_z: under_parts must not be empty")

    max_z = None
    for p in parts_list:
        ov = col & p
        if _vol(ov) > 0:
            top = ov.bounding_box().max.Z
            max_z = top if max_z is None else max(max_z, top)
    _need(max_z is not None, "under_board_top_z: column did not intersect any under_parts")
    return {"max_top_z": round(float(max_z), 3)}


def floating_stop_check(
    floating_part,
    stop_part,
    pcb_part,
    gap_dz: float,
    stop_dz: float,
    tol: float = 0.01,
) -> dict:
    """Positive + negative control for under-board floating parts like straps/electrodes (height-stack.md §3).

    1. At small upward play `gap_dz`, floating_part must clear both `stop_part` and `pcb_part`.
    2. At `stop_dz`, floating_part must engage `stop_part` (clash > tol) while still clearing `pcb_part`
       so the PCB never acts as the mechanical hard stop.
    """
    _need(0 < gap_dz < stop_dz, "floating_stop_check: requires 0 < gap_dz < stop_dz")
    f_gap = _translate_solids(floating_part, (0.0, 0.0, float(gap_dz)))
    f_stop = _translate_solids(floating_part, (0.0, 0.0, float(stop_dz)))
    stop_sols = stop_part.solids() if hasattr(stop_part, "solids") else [stop_part]
    pcb_sols = pcb_part.solids() if hasattr(pcb_part, "solids") else [pcb_part]

    gap_stop = sum(clash(a, b) for a in f_gap for b in stop_sols)
    gap_pcb = sum(clash(a, b) for a in f_gap for b in pcb_sols)
    hit_stop = sum(clash(a, b) for a in f_stop for b in stop_sols)
    hit_pcb = sum(clash(a, b) for a in f_stop for b in pcb_sols)

    ok = (gap_stop <= tol) and (gap_pcb <= tol) and (hit_stop > tol) and (hit_pcb <= tol)
    return {
        "clear": ok,
        "at_gap_dz": {"dz": gap_dz, "stop_clash": round(gap_stop, 4), "pcb_clash": round(gap_pcb, 4)},
        "at_stop_dz": {"dz": stop_dz, "stop_clash": round(hit_stop, 4), "pcb_clash": round(hit_pcb, 4)},
    }
