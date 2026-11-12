"""Author the pillow mother surfaces in Autodesk Fusion via the official Fusion MCP
(featureType=script). Paste this file's content as the `script` argument.

Produces vendor/fusion/{pillow_solid,pillow_shell_2p5,pillow_shell_2p65,pillow_shell_4p25}.step
which models/lobster_common.py consumes (FUSION_PILLOW). Change the radii here AND in
lobster_common.py, re-run, then `scripts/gen models/lb_*.step.py --force --write`.

Fusion API notes learned the hard way:
  - a Part (零件) document allows ONE component -> build bodies in root, no sub-components
  - FilletFeatureInput.addVariableRadiusEdgeSet(edges, start, end, positions:list, radii:list)
  - ShellFeatures.createInput(bodies_collection, isTangentChain) + insideThickness = hollow solid
  - exportManager exports the whole root -> build one body at a time, export, deleteMe()
"""
import adsk.core, adsk.fusion, os


def run(_context: str):
    app = adsk.core.Application.get()
    ui = app.userInterface
    # ---- PREFLIGHT (run discipline, verified 2026-09-04) ----
    # 1. modal guard: never execute while a modal command is up
    assert ui.activeCommand == 'SelectCommand', f'modal command active: {ui.activeCommand}'
    # 2. checkpoint: saved docs get a version before the first write
    #    (the MCP now has execute featureType="document" operation="save" — prefer
    #    that as a separate call; in-script fallback: app.activeDocument.save('pre-AI'))
    doc = app.activeDocument
    if doc.isSaved and doc.isModified:
        raise RuntimeError('unsaved changes present — save/checkpoint first')
    des = adsk.fusion.Design.cast(app.activeProduct)
    root = des.rootComponent
    # ---- USER PARAMETERS: bind dims to named params so the design stays
    # editable in Fusion's parameter table (function modeling, verified):
    #   up = des.userParameters; VS = adsk.core.ValueInput.createByString
    #   up.add('pw', VS('90 mm'), 'mm', 'width')      # once, idempotent via itemByName
    #   sketchDim.parameter.expression = 'pw'          # sketch binding
    #   ein.setDistanceExtent(False, VS('ph'))         # feature binding
    #   fillet radii take VS('r_corner'); unitless scalars (variable-radius
    #   positions 0..1) STAY createByReal. After editing a param:
    #   adsk.doEvents() before reading geometry back.
    cm = 0.1
    P = adsk.core.Point3D.create
    V = adsk.core.ValueInput.createByReal
    W, D, H, Z0 = 90*cm, 64*cm, 64*cm, 2*cm                 # pillow z 2..66
    R_CORNER, R_FRONT, R_BACK, BOT = 20*cm, 10*cm, 22*cm, 8*cm  # = lobster_common corner_r / top_r_front / top_r_back / bot_r
    OUT = os.environ.get("FUSION_OUT_DIR", os.path.expanduser("~/go/202608/lobster-box/vendor/fusion"))
    for b in list(root.bRepBodies):
        b.deleteMe()

    def pillow(name):
        sk = root.sketches.add(root.xYConstructionPlane)
        rect = sk.sketchCurves.sketchLines.addCenterPointRectangle(P(0, 0, 0), P(W/2, D/2, 0))
        arcs = sk.sketchCurves.sketchArcs
        for i in range(4):
            a, b = rect.item(i), rect.item((i+1) % 4)
            arcs.addFillet(a, a.endSketchPoint.geometry, b, b.startSketchPoint.geometry, R_CORNER)
        ein = root.features.extrudeFeatures.createInput(sk.profiles.item(0), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        ein.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create(V(H)), adsk.fusion.ExtentDirections.PositiveExtentDirection)
        ein.startExtent = adsk.fusion.OffsetStartDefinition.create(V(Z0))
        body = root.features.extrudeFeatures.add(ein).bodies.item(0)
        body.name = name
        fin = root.features.filletFeatures.createInput()
        front_c = adsk.core.ObjectCollection.create(); back_c = adsk.core.ObjectCollection.create()
        for e in body.edges:                       # top loop: front R_FRONT, back R_BACK, sides blend
            bb = e.boundingBox
            if not (abs(bb.minPoint.z - (Z0+H)) < 1e-4 and abs(bb.maxPoint.z - (Z0+H)) < 1e-4):
                continue
            yc = (bb.minPoint.y + bb.maxPoint.y) / 2
            if e.geometry.curveType == adsk.core.Curve3DTypes.Arc3DCurveType or abs(bb.maxPoint.y - bb.minPoint.y) < 1e-4:
                (front_c if yc > 0 else back_c).add(e)
            else:
                coll = adsk.core.ObjectCollection.create(); coll.add(e)
                r0, r1 = (R_FRONT, R_BACK) if e.startVertex.geometry.y > 0 else (R_BACK, R_FRONT)
                fin.addVariableRadiusEdgeSet(coll, V(r0), V(r1), [V(0.5)], [V((R_FRONT+R_BACK)/2)])
        fin.addConstantRadiusEdgeSet(front_c, V(R_FRONT), False)
        fin.addConstantRadiusEdgeSet(back_c, V(R_BACK), False)
        root.features.filletFeatures.add(fin)
        bot = adsk.core.ObjectCollection.create()
        for e in body.edges:
            bb = e.boundingBox
            if abs(bb.minPoint.z - Z0) < 1e-4 and abs(bb.maxPoint.z - Z0) < 1e-4:
                bot.add(e)
        fin2 = root.features.filletFeatures.createInput()
        fin2.addConstantRadiusEdgeSet(bot, V(BOT), True)
        root.features.filletFeatures.add(fin2)
        return body

    res = []
    for name, t in (("pillow_shell_2p5", 2.5*cm), ("pillow_shell_2p65", 2.65*cm),
                    ("pillow_shell_4p25", 4.25*cm), ("pillow_solid", None)):
        body = pillow(name)
        if t is not None:
            bodies = adsk.core.ObjectCollection.create(); bodies.add(body)
            sin = root.features.shellFeatures.createInput(bodies, True)
            sin.insideThickness = V(t)
            root.features.shellFeatures.add(sin)
        opts = des.exportManager.createSTEPExportOptions(os.path.join(OUT, name + ".step"), root)
        des.exportManager.execute(opts)
        res.append(f"{name}: faces={body.faces.count} vol_mm3={body.volume/(cm**3):.0f}")
        if t is not None:
            body.deleteMe()
    app.activeViewport.fit()
    print("\n".join(res))
