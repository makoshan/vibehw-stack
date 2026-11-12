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

    from fitprobe import clash, fits, envelope, wall_at, solids, outside
"""

from __future__ import annotations

from build123d import Axis, Box, Location, Pos, Solid


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
