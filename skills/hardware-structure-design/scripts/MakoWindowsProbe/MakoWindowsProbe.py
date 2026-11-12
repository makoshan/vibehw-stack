import adsk.core, adsk.fusion
import json, os, time, traceback

def run(context):
    app = adsk.core.Application.get()
    out = os.path.join(os.path.expanduser('~'), 'codex-tools', 'fusion-test')
    os.makedirs(out, exist_ok=True)
    report = {'started': time.time(), 'ok': False}
    try:
        if app.userInterface.activeCommand != 'SelectCommand':
            raise RuntimeError('Finish the current Fusion command before running the probe')
        doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        doc.name = 'Mako Windows Probe'
        design = adsk.fusion.Design.cast(app.activeProduct)
        design.designType = adsk.fusion.DesignTypes.ParametricDesignType
        root = design.rootComponent
        sketch = root.sketches.add(root.xYConstructionPlane)
        sketch.sketchCurves.sketchLines.addTwoPointRectangle(
            adsk.core.Point3D.create(0, 0, 0), adsk.core.Point3D.create(2, 1, 0))
        feature = root.features.extrudeFeatures.addSimple(
            sketch.profiles.item(0), adsk.core.ValueInput.createByString('3 mm'),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        body = feature.bodies.item(0)
        volume = body.volume * 1000
        assert abs(volume - 600) < 0.01, volume
        tag = time.strftime('%Y%m%d-%H%M%S')
        stem = os.path.join(out, 'probe-' + tag)
        export = design.exportManager
        assert export.execute(export.createFusionArchiveExportOptions(stem + '.f3d'))
        assert export.execute(export.createSTEPExportOptions(stem + '.step'))
        report.update(ok=True, version=app.version, volume_mm3=volume,
                      bodies=root.bRepBodies.count, output=stem)
        app.activeViewport.fit()
    except Exception:
        report['error'] = traceback.format_exc()
    report['elapsed_seconds'] = time.time() - report['started']
    with open(os.path.join(out, 'result.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    app.userInterface.messageBox('Windows probe: ' + ('PASS' if report['ok'] else 'FAILED') + '\n' + out)
