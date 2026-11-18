"""Blender 无界面产品影棚：用同一场景出俯视、斜视和可选的接口特写。

输入：.pcb3d（pcb2blender）、.glb/.gltf、.stl、.obj。支持平铺与保留装配坐标。
用法：
  blender -b --factory-startup --python-exit-code 1 --python studio.py -- \
      [--bg dark|light] [--samples 128] [--scale 100] [--expo 1.0] [--name out] <out_dir> <模型>...
出图：<out_dir>/<name>-<视角>-<bg>.png。
"""
import argparse, math, os, re, sys
from pathlib import Path


def millimetres(value):
    try:
        xyz = tuple(float(n) for n in value.split(','))
    except ValueError:
        xyz = ()
    if len(xyz) != 3 or not all(math.isfinite(n) for n in xyz):
        raise argparse.ArgumentTypeError('需要三个毫米坐标 X,Y,Z')
    return xyz


def positive(value):
    num = float(value)
    if not math.isfinite(num) or num <= 0:
        raise argparse.ArgumentTypeError('需要有限正数')
    return num


def camera_views(value):
    views = value.split(',')
    if any(view not in ('top', 'iso', 'detail') for view in views):
        raise argparse.ArgumentTypeError('视角用逗号分隔，可选 top,iso,detail')
    return tuple(dict.fromkeys(views))


def elevation(value):
    angle = positive(value)
    if angle >= 90:
        raise argparse.ArgumentTypeError('仰角要小于 90°，垂直俯拍用 top')
    return angle


ap = argparse.ArgumentParser()
ap.add_argument('--bg', choices=['dark', 'light'], default='dark')
ap.add_argument('--layout', choices=['spread', 'assembled'], default='spread',
                help='平铺；或保留输入间的共同装配坐标，仅统一落地')
ap.add_argument('--spread-gap-mm', type=positive, help='平铺时的等距留白，单位 mm')
ap.add_argument('--no-tray', action='store_true', help='黑底不生成托盘，适合模型自带底座')
ap.add_argument('--views', type=camera_views, default='top,iso', help='用逗号分隔 top,iso,detail')
ap.add_argument('--detail-target-mm', type=millimetres,
                help='落地后 Blender 世界坐标 X,Y,Z，单位 mm')
ap.add_argument('--detail-width-mm', type=positive, default=18,
                help='特写在对焦平面上的横向宽度，单位 mm')
ap.add_argument('--detail-fstop', type=positive, default=14, help='特写光圈，数值越大景深越深')
ap.add_argument('--detail-azimuth', type=float, default=-120, help='从 +X 向 +Y 的方位角，单位度')
ap.add_argument('--detail-elevation', type=elevation, default=49, help='从水平面向上的仰角，单位度')
ap.add_argument('--width', type=int, default=2400)
ap.add_argument('--height', type=int, default=1350)
ap.add_argument('--samples', type=int, default=128)
ap.add_argument('--scale', type=int, default=100)
ap.add_argument('--expo', type=float, default=1.0, help='整体照度倍率')
ap.add_argument('--exposure', type=float, help='胶片曝光（档），默认按背景取预设')
ap.add_argument('--gold', help='沉金颜色 sRGB 十六进制；默认黑底 c8964a、浅色 e89a10')
ap.add_argument('--tray', default='0.007,0.009,0.014', help='黑底托盘线性 RGB')
ap.add_argument('--tray-spec', type=float, default=0.1, help='托盘反光（Principled Specular IOR Level）')
ap.add_argument('--floor', default='0.92,0.95,1.0', help='浅色地面/背景线性 RGB')
ap.add_argument('--gold-rough', type=float, default=0.3)
ap.add_argument('--ic-black', type=float, default=0.012,
                help='芯片外壳改成的线性灰度（0.012 ≈ sRGB 28 近黑）；0 = 保留模型原色')
ap.add_argument('--ic-names', default=r'QFN|DFN|QFP|SOIC|SSOP|TSSOP|MSOP|SON\b|SOT-|SOD-|BGA|LGA|CSP|^RHL|^RGE|^RSW|^L_0',
                help='按器件名认芯片/电感，把它顶面材质改黑（正则）')
ap.add_argument('--name', default='product')
ap.add_argument('out_dir', type=Path)
ap.add_argument('inputs', type=Path, nargs='+')
a = ap.parse_args(sys.argv[sys.argv.index('--') + 1:])
if 'detail' in a.views and a.detail_target_mm is None:
    ap.error('detail 视角需要 --detail-target-mm=X,Y,Z')
if not math.isfinite(a.detail_azimuth):
    ap.error('特写方位角必须为有限数值')
if a.spread_gap_mm is not None and a.layout != 'spread':
    ap.error('--spread-gap-mm 只适用于 spread')
if min(a.width, a.height, a.samples) < 1 or not 1 <= a.scale <= 100:
    ap.error('宽、高、采样数必须为正整数，缩放比例须为 1–100')
a.out_dir = a.out_dir.resolve()
a.out_dir.mkdir(parents=True, exist_ok=True)
a.gold = a.gold or ('c8964a' if a.bg == 'dark' else 'e89a10')
a.tray = tuple(map(float, a.tray.split(',')))
a.floor = tuple(map(float, a.floor.split(',')))
a.ic_color = (a.ic_black, a.ic_black, a.ic_black * 1.08) if a.ic_black > 0 else None
CACHE = Path(os.environ.get('PRODUCT_RENDER_CACHE', Path.home() / '.cache/product-render'))

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from bpy_extras.object_utils import world_to_camera_view  # noqa: E402
from mathutils import Vector  # noqa: E402

sc = bpy.context.scene
for o in list(bpy.data.objects):   # 清掉默认立方体/灯/相机
    bpy.data.objects.remove(o, do_unlink=True)


def import_one(path: Path):
    ext = path.suffix.lower()
    if ext == '.pcb3d':
        if 'pcb2blender_importer' not in sys.modules:
            sys.path[:0] = [str(CACHE / 'deps'), str(CACHE / 'pcb2blender')]
            import pcb2blender_importer
            pcb2blender_importer.register()
        r = bpy.ops.pcb2blender.import_pcb3d(filepath=str(path), add_solder_joints='SMART', texture_dpi=2032.0,
                                            center_boards=a.layout == 'spread')
    elif ext in ('.glb', '.gltf'):
        r = bpy.ops.import_scene.gltf(filepath=str(path))
    elif ext == '.stl':
        r = bpy.ops.wm.stl_import(filepath=str(path))
    elif ext == '.obj':
        r = bpy.ops.wm.obj_import(filepath=str(path))
    else:
        sys.exit(f'不支持的格式 {path}')
    assert r == {'FINISHED'}, (path, r)


def bbox(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs if o.type == 'MESH' for c in o.bound_box]
    assert pts, '没有网格'
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    return lo, hi


# 平铺时逐件落地。装配模式保留部件相对位姿，全部导入后一起落地。
cursor, content = 0.0, []
for p in a.inputs:
    before = set(bpy.data.objects)
    import_one(p.resolve())
    new = [o for o in bpy.data.objects if o not in before]
    bpy.context.view_layer.update()
    lo, hi = bbox(new)
    for o in new:
        if o.type == 'LIGHT':
            o.hide_render = True
    if a.layout == 'spread':
        gap = 0
        if content:
            gap = a.spread_gap_mm / 1000 if a.spread_gap_mm is not None else 0.15 * max(hi - lo)
        off = Vector((cursor - lo.x + gap, -(lo.y + hi.y) / 2, -lo.z))
        for o in new:
            if o.parent not in new:
                o.location += off
        cursor += hi.x - lo.x + gap
    content += new
    print('IMPORTED', p.name, 'size_mm', [round(x * 1000, 2) for x in hi - lo], 'objects', len(new))

if a.layout == 'assembled':
    lo, _ = bbox(content)
    for o in content:
        if o.parent is None:
            o.location.z -= lo.z
bpy.context.view_layer.update()



def retint_finish():
    # pcb2blender 的 ENIG 是 #efdfbb、粗糙度 0.1：浅香槟色的镜面，只映出环境，渲出来发灰发褐。
    # 参考图沉金实测 sRGB(174–226,131–180,64–78)、缎面，改成 CUSTOM 橙金 + 粗糙度 0.3。
    rgb = [int(a.gold[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    n = 0
    for tree in [m.node_tree for m in bpy.data.materials if m.node_tree] + list(bpy.data.node_groups):
        for node in tree.nodes:
            if 'SurfaceFinish' in node.bl_idname and hasattr(node, 'surface_finish'):
                if node.surface_finish in ('ENIG', 'CUSTOM'):
                    node.surface_finish = 'CUSTOM'
                    node.inputs['Color'].default_value = (*lin, 1)
                    node.inputs['Roughness'].default_value = a.gold_rough
                    n += 1
    print('GOLD_NODES', n)


def blacken_ic_bodies():
    # KiCad 模型的芯片/电感外壳是中灰塑料（SOT-23 线性 0.147 ≈ sRGB 107）、半哑光，再映出灰环境，
    # 渲出来浅灰；参考图是近黑哑光环氧。只改「中性灰的深色塑料」：带色的（棕电容、绿 LED）和浅色的
    # （白连接器）不动。
    n, variants = 0, set()
    for m in bpy.data.materials:
        for node in (m.node_tree.nodes if m.node_tree else []):
            if 'Mat4cad' not in node.bl_idname or getattr(node, 'mat_base', '') != 'PLASTIC':
                continue
            c = node.inputs['Color'].default_value
            if max(c[:3]) < 0.3 and max(c[:3]) - min(c[:3]) < 0.05:
                node.inputs['Color'].default_value = (*a.ic_color, 1)
                try:
                    node.mat_variant = 'MATTE'
                except TypeError:   # 这版 mat4cad 没有 MATTE 就保留原变体
                    pass
                variants.add(node.mat_variant)
                n += 1
    print('IC_BLACK_NODES', n, sorted(variants))
    # 有的模型（例 TI VQFN24 RHL0024A）外壳被 mat4cad 判成「金属」，按塑料挑不出来。
    # 补一条：名字像芯片封装的器件，把它最顶上那层朝上的面所用的材质改黑（先复制，不连累共用该材质的件）。
    pat = re.compile(a.ic_names, re.I)
    hits = []
    for o in bpy.context.scene.objects:
        if o.type != 'MESH' or o.name.startswith('SOLDER') or not pat.search(o.name) or not o.material_slots:
            continue
        mw, area = o.matrix_world, {}
        nrm = mw.to_3x3()
        tops = [(p, (mw @ p.center).z) for p in o.data.polygons if (nrm @ p.normal).normalized().z > 0.9]
        if not tops:
            continue
        zmax = max(z for _, z in tops)
        for p, z in tops:
            if z > zmax - 0.05e-3:
                area[p.material_index] = area.get(p.material_index, 0) + p.area
        slot = o.material_slots[max(area, key=area.get)]
        m = slot.material.copy()
        slot.material = m
        for node in m.node_tree.nodes:
            if 'Mat4cad' in node.bl_idname:
                node.mat_base, node.mat_variant = 'PLASTIC', 'MATTE'
                node.inputs['Color'].default_value = (*a.ic_color, 1)
        hits.append(o.name)
    print('IC_TOP_BLACK', len(hits), hits[:8])


retint_finish()
if a.ic_color:
    blacken_ic_bodies()
lo, hi = bbox(content)
size, center = hi - lo, (hi + lo) / 2
L = max(size.x, size.y)
print('CONTENT_BOUNDS_MM', [round(x * 1000, 3) for x in lo], [round(x * 1000, 3) for x in hi], flush=True)
if 'detail' in a.views:
    detail_target = Vector(tuple(x / 1000 for x in a.detail_target_mm))
    # 日志只显示到 0.001 mm，同时容纳模型导入后的浮点舍入。
    if not all(lo[i] - 1e-6 <= detail_target[i] <= hi[i] + 1e-6 for i in range(3)):
        ap.error('特写中心落在主体范围之外，请按 CONTENT_BOUNDS_MM 复核坐标')


def mat(name, rgb, rough, metal=0.0, spec=0.5):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes['Principled BSDF']
    b.inputs['Base Color'].default_value = (*rgb, 1)
    b.inputs['Roughness'].default_value = rough
    b.inputs['Metallic'].default_value = metal
    b.inputs['Specular IOR Level'].default_value = spec
    return m


world = bpy.data.worlds.new('w')
sc.world = world
world.use_nodes = True
bgnode = world.node_tree.nodes['Background']
frame_objs = list(content)
if a.bg == 'dark':
    # 纯黑世界 + 深蓝灰圆角托盘（参考图 4；颜色目测 sRGB(44,49,58) 转线性）
    # 相机看到纯黑，但反射/补光看到中灰：否则沉金焊盘只反射黑色，看不出金色（第一版就是）
    nt = world.node_tree
    bgnode.inputs['Color'].default_value = (0.5, 0.52, 0.56, 1)
    bgnode.inputs['Strength'].default_value = 0.6 * a.expo
    black = nt.nodes.new('ShaderNodeBackground')
    black.inputs['Color'].default_value = (0, 0, 0, 1)
    lp, mix = nt.nodes.new('ShaderNodeLightPath'), nt.nodes.new('ShaderNodeMixShader')
    nt.links.new(lp.outputs['Is Camera Ray'], mix.inputs['Fac'])
    nt.links.new(bgnode.outputs['Background'], mix.inputs[1])
    nt.links.new(black.outputs['Background'], mix.inputs[2])
    nt.links.new(mix.outputs['Shader'], nt.nodes['World Output'].inputs['Surface'])
    if not a.no_tray:
        bpy.ops.mesh.primitive_cube_add(size=1, location=(center.x + L * 0.12, center.y, -0.0015 * L / 0.04))
        tray = bpy.context.object
        tray.scale = (size.x + L * 0.55, size.y + L * 0.30, 0.003 * L / 0.04)
        bpy.ops.object.transform_apply(scale=True)
        bev = tray.modifiers.new('bevel', 'BEVEL')
        bev.width, bev.segments, bev.affect = L * 0.08, 16, 'EDGES'
        # 托盘亮度主要来自环境反射。低反光、高粗糙度保留深蓝灰。
        tray.data.materials.append(mat('tray', a.tray, 0.8, spec=a.tray_spec))
        frame_objs.append(tray)
else:
    # 浅冷灰无缝影棚（参考图 1–3）：大地面 + 同色世界补一点环境光
    grey = a.floor   # 参考图 1–3 背景接近白（实测亮度中位 181–231）
    bgnode.inputs['Color'].default_value = (*grey, 1)
    bgnode.inputs['Strength'].default_value = 0.25 * a.expo
    bpy.ops.mesh.primitive_plane_add(size=L * 60, location=(center.x, center.y, 0))
    bpy.context.object.data.materials.append(mat('floor', grey, 0.8))


def area(name, loc, size_m, irradiance, aim):
    # 按目标照度反推功率 P ≈ E·π·d²（E 单位 W/m²，Blender 里 1–5 是日光量级）
    d = bpy.data.lights.new(name, 'AREA')
    d.size = size_m
    d.energy = irradiance * math.pi * (loc - aim).length ** 2
    o = bpy.data.objects.new(name, d)
    o.location = loc
    o.rotation_euler = (aim - loc).to_track_quat('-Z', 'Y').to_euler()
    bpy.context.collection.objects.link(o)


H = L * 4
E = a.expo * (1.0 if a.bg == 'dark' else 0.8)
# 顶部大柔光，偏离正上方约 40°：正上方时俯视图里平的金属焊盘把它镜面反射成白点（第一版就是）
area('key', center + Vector((-0.6 * H, 0.55 * H, H)), L * 3.0, 2.5 * E, center)
area('side', center + Vector((-H, -H * 0.3, H * 0.45)), L * 1.2, 1.0 * E, center)  # 低角度侧光，打器件立体感
area('rim', center + Vector((H * 0.8, H * 0.8, H * 0.5)), L * 0.8, 0.3 * E, center)

sc.render.engine = 'CYCLES'
sc.cycles.samples = a.samples
sc.cycles.use_denoising = True
try:
    prefs = bpy.context.preferences.addons['cycles'].preferences
    prefs.compute_device_type = 'METAL'
    prefs.get_devices()
    for d in prefs.devices:
        d.use = True
    sc.cycles.device = 'GPU'
except Exception as e:  # 没 GPU 就 CPU，结果一样只是慢
    print('GPU_FALLBACK', e)
sc.view_settings.view_transform = 'AgX'
# 参考图是高调、低对比：黑底图亮度中位 37、高光 217，第一版只有 13 / 80
sc.view_settings.look = 'AgX - Base Contrast'
sc.view_settings.exposure = a.exposure if a.exposure is not None else (1.2 if a.bg == 'dark' else 1.9)
sc.render.resolution_x, sc.render.resolution_y = a.width, a.height
sc.render.resolution_percentage = a.scale
sc.render.image_settings.file_format = 'PNG'

cam_d = bpy.data.cameras.new('cam')
cam = bpy.data.objects.new('cam', cam_d)
bpy.context.collection.objects.link(cam)
sc.camera = cam
cam_d.clip_start, cam_d.clip_end = L * 0.01, L * 200
corners = [o.matrix_world @ Vector(c) for o in frame_objs if o.type == 'MESH' for c in o.bound_box]


def in_frame(margin):
    bpy.context.view_layer.update()
    return all(margin <= v.x <= 1 - margin and margin <= v.y <= 1 - margin and v.z > 0
               for v in (world_to_camera_view(sc, cam, p) for p in corners))


def shoot(view):
    cam_d.dof.use_dof = view == 'detail'
    cam_d.sensor_fit = 'AUTO'
    cam_d.clip_start = L * 0.01
    cam_d.clip_end = L * 200
    if view == 'top':
        # 正交正俯视。旋转 (0,0,0) = 看 -Z、上方 +Y，和 KiCad 俯视同向；
        # 别用 to_track_quat 算竖直视线：方向退化，会随机转 90°（第一版就转了）
        cam_d.type = 'ORTHO'
        cam.rotation_euler = (0, 0, 0)
        flo, fhi = Vector([min(p[i] for p in corners) for i in range(3)]), Vector([max(p[i] for p in corners) for i in range(3)])
        cam.location = ((flo.x + fhi.x) / 2, (flo.y + fhi.y) / 2, hi.z + H)
        aspect = sc.render.resolution_x / sc.render.resolution_y
        cam_d.ortho_scale = max(fhi.x - flo.x, (fhi.y - flo.y) * aspect) * 1.18 / min(1, aspect)
    elif view == 'iso':
        # 斜视透视：从左前上方看，距离从远往近收，取最后一个完整入画的位置
        cam_d.type, cam_d.lens = 'PERSP', 85
        dirv = Vector((-0.45, -0.8, 0.75)).normalized()
        cam.rotation_euler = (-dirv).to_track_quat('-Z', 'Y').to_euler()
        d = L * 20
        cam.location = center + dirv * d
        assert in_frame(0.06), '斜视最远处仍出画'
        while True:
            cam.location = center + dirv * d * 0.95
            if not in_frame(0.06):
                cam.location = center + dirv * d
                break
            d *= 0.95
    else:
        # 用画面在焦平面上的横宽定构图。特写主动裁切外围，焦点由真实接口坐标决定。
        cam_d.type, cam_d.lens = 'PERSP', 85
        cam_d.sensor_fit = 'HORIZONTAL'
        azimuth, tilt = map(math.radians, (a.detail_azimuth, a.detail_elevation))
        direction = Vector((math.cos(tilt) * math.cos(azimuth),
                            math.cos(tilt) * math.sin(azimuth), math.sin(tilt)))
        distance = (a.detail_width_mm / 1000) * cam_d.lens / cam_d.sensor_width
        cam.location = detail_target + direction * distance
        cam.rotation_euler = (-direction).to_track_quat('-Z', 'Y').to_euler()
        cam_d.clip_start = min(cam_d.clip_start, distance / 100)
        cam_d.clip_end = max(cam_d.clip_end, distance + L * 2)
        cam_d.dof.focus_distance = distance
        cam_d.dof.aperture_fstop = a.detail_fstop
        print('DETAIL_FOCUS_MM', a.detail_target_mm, 'width_mm', a.detail_width_mm, 'fstop', a.detail_fstop, flush=True)
    if view != 'detail' and not in_frame(0.01):
        sys.exit(f'{view}: 模型出画')
    out = a.out_dir / f'{a.name}-{view}-{a.bg}.png'
    sc.render.filepath = str(out)
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(str(out))
    px = np.empty(img.size[0] * img.size[1] * 4, np.float32)
    img.pixels.foreach_get(px)
    rgb = px.reshape(-1, 4)[:, :3]
    clipped = float((rgb.min(axis=1) >= 0.995).mean())
    print('RENDERED', out, 'clipped', round(clipped, 4), 'mean', round(float(rgb.mean()), 3))
    if clipped > 0.01:   # 正常出图白点 <1%；整张过曝时这一项是几十 %
        sys.exit(f'{view}: 过曝 {clipped:.1%}，调小 --expo')


for v in a.views:
    shoot(v)
