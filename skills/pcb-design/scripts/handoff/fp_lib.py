"""消掉 KiCad DRC 的两类封装警告：KiCad 自带 Python 运行。
用法：KPY fp_lib.py 源板 输出板（输出板旁边写 C08A.pretty 和 fp-lib-table）
  lib_footprint_issues（「当前配置中不包含封装库 'C08A'」，164 条）：板上的封装原样导成工程自带的库 C08A.pretty
    （摆正角度、背面件翻回正面、放到原点），工程 fp-lib-table 指向它；没写库名的 3 个座子也挂进这个库。
  missing_courtyard（H901–H904 安装孔，4 条）：照孔外缘 + 0.25 mm 补一圈外框（同面 CrtYd 层）。
铜、焊盘、位置都不动；改完用 copper_hash.py 证明铜没变。"""
import os, sys
import pcbnew as k

src, dst = sys.argv[1], sys.argv[2]
b = k.LoadBoard(src)
LIB = 'C08A'
libdir = os.path.join(os.path.dirname(os.path.abspath(dst)), LIB + '.pretty')
IO = k.PCB_IO_MGR.FindPlugin(k.PCB_IO_MGR.KICAD_SEXP)          # 直接用 KiCad 格式插件存库（FootprintSave 按路径猜插件，猜不出）
os.makedirs(libdir, exist_ok=True)                           # .pretty 库就是一个目录（KiCad 10 的 python 建库接口是坏的）
added_cy = []
for f in b.GetFootprints():
    has_cy = any(g.GetLayer() in (k.F_CrtYd, k.B_CrtYd) for g in f.GraphicalItems())
    if not has_cy:
        r = max((p.GetPosition() - f.GetPosition()).EuclideanNorm() + max(p.GetSize().x, p.GetSize().y) // 2 for p in f.Pads())
        c = k.PCB_SHAPE(f, k.SHAPE_T_CIRCLE)
        c.SetCenter(f.GetPosition()); c.SetEnd(f.GetPosition() + k.VECTOR2I(r + k.FromMM(.25), 0))
        c.SetLayer(k.B_CrtYd if f.IsFlipped() else k.F_CrtYd); c.SetWidth(k.FromMM(.05))
        f.Add(c); added_cy.append(f.GetReference())
    if str(f.GetFPID().GetLibNickname()) != LIB:
        f.SetFPID(k.LIB_ID(LIB, str(f.GetFPID().GetLibItemName())))
saved = set()
for f in b.GetFootprints():
    name = str(f.GetFPID().GetLibItemName())
    if name in saved:
        continue
    g = k.FOOTPRINT(f)                                        # 拷一份，摆回库里的姿态再存
    if g.IsFlipped():
        g.Flip(g.GetPosition(), k.FLIP_DIRECTION_TOP_BOTTOM)
    g.SetOrientationDegrees(0)
    g.SetPosition(k.VECTOR2I(0, 0))
    IO.FootprintSave(libdir, g)
    saved.add(name)
open(os.path.join(os.path.dirname(os.path.abspath(dst)), 'fp-lib-table'), 'w').write(
    '(fp_lib_table\n  (version 7)\n  (lib (name "%s")(type "KiCad")(uri "${KIPRJMOD}/%s.pretty")(options "")(descr "C08B 采集板封装，从板上原样导出"))\n)\n' % (LIB, LIB))
b.Save(dst)
print('补外框', added_cy, '入库封装', len(saved))
