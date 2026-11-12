"""导出给别的 EDA 前用：把泪滴区的外形换成 KiCad 已裁好的填充形状（另存一份，源板不动）。
KiCad 的泪滴区外形比实际铜大，填充时才按间距裁掉；嘉立创导入时把外形当实心铜，C08B D5 因此报 18 条间距错误、1 处 0 mil 碰到异网过孔。
用法：KiCad python flatten_teardrops.py 入板 出板"""
import sys, pcbnew as k
b = k.LoadBoard(sys.argv[1]); n = 0; drop = []
for z in b.Zones():
    if not z.IsTeardropArea(): continue
    L = z.GetLayer()
    if not z.HasFilledPolysForLayer(L) or z.GetFilledPolysList(L).OutlineCount() == 0:
        drop.append(z); continue   # 被裁没了的泪滴删掉
    fill = k.SHAPE_POLY_SET(z.GetFilledPolysList(L))   # 先拷一份
    ol = z.Outline(); ol.RemoveAllContours(); ol.Append(fill); n += 1   # 原地换外形（SetOutline 会段错误）
for z in drop: b.Remove(z)
b.Save(sys.argv[2]); print('泪滴外形换成填充', n, '删掉空的', len(drop))
