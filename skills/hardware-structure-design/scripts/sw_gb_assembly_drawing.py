#!/usr/bin/env python3
"""SOLIDWORKS 远程预检与国标（GB/T）机械装配工程图 `.SLDASM` + `.SLDDRW` 自动生成工具。

状态边界（2026-11-23 真机实测验证）：
- 已在 SOLIDWORKS 2025 (`33.5.0`) + `pywin32` 晚绑定宿主实测跑通 `.SLDPRT` + `.SLDASM` + 国标第一角全剖工程图 + 气球序号 + `180 mm` 齐边自下而上明细栏（通用生成器默认输出主/俯/全剖/轴测四视图；项目级局部放大图 `CreateDetailViewAt4` 见参考文档）。
- 预检门禁、动态图幅内框坐标校验与正反例自测由 `--self-test` 离线守护。

用法：
1. `--self-test`：离线运行正反例门禁与 Mock SOLIDWORKS 执行测试（无需连接远端）。
2. `--preflight-only`：仅经 SSH 探测远端 `.solidworks-cad-writer.lock` 与 `SolidWorks Flexnet Server` 状态。
3. `--spec <spec.json> --emit-calls <calls.json>`：纯本地生成供 `solidworks_remote.py` 执行的调用包（加 `--preflight-only` 则同时做远端预检）。
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys


DEFAULT_CONFIG = Path.home() / ".config/mako-cad/solidworks.json"

# GB/T 14689-2008 留装订边图框规格：(宽 m, 高 m, 周边 c m)；左装订边统一 a = 0.025 m；标题栏 GB/T 10609.1 高 0.056 m
GB_BINDING_A_M = 0.025
GB_TITLE_BLOCK_H_M = 0.056
GB_SHEETS_M = {
    "a0": (1.189, 0.841, 0.010),
    "a1": (0.841, 0.594, 0.010),
    "a2": (0.594, 0.420, 0.010),
    "a3": (0.420, 0.297, 0.005),
    "a4": (0.297, 0.210, 0.005),
}


def compute_sheet_layout_m(sheet_size: str) -> dict[str, float]:
    """根据图幅动态计算内框边界、各视图中心与明细栏右下锚点，并硬断言全部落在内框安全区内。"""
    key = sheet_size.lower()
    if key not in GB_SHEETS_M:
        raise ValueError(f"Unsupported GB sheet_size: {sheet_size}")
    w_m, h_m, c_m = GB_SHEETS_M[key]
    ix0, ix1 = GB_BINDING_A_M, w_m - c_m
    iy0, iy1 = c_m, h_m - c_m
    iw, ih = ix1 - ix0, iy1 - iy0

    layout = {
        "w_m": w_m,
        "h_m": h_m,
        "c_m": c_m,
        "ix0": round(ix0, 4),
        "ix1": round(ix1, 4),
        "iy0": round(iy0, 4),
        "iy1": round(iy1, 4),
        "front_x": round(ix0 + 0.23 * iw, 4),
        "front_y": round(iy0 + 0.68 * ih, 4),
        "top_x": round(ix0 + 0.23 * iw, 4),
        "top_y": round(iy0 + 0.30 * ih, 4),
        "sec_x": round(ix0 + 0.53 * iw, 4),
        "sec_y": round(iy0 + 0.68 * ih, 4),
        "iso_x": round(ix0 + 0.82 * iw, 4),
        "iso_y": round(iy0 + 0.72 * ih, 4),
        "bom_x": round(ix1, 4),
        "bom_y": round(iy0 + GB_TITLE_BLOCK_H_M, 4),
    }
    validate_layout_bounds(layout)
    return layout


def validate_layout_bounds(layout: dict[str, float], margin_m: float = 0.015) -> None:
    """断言所有视图中心严格位于图框内框且留有安全边距，明细栏锚点不压标题栏。"""
    ix0, ix1 = layout["ix0"], layout["ix1"]
    iy0, iy1 = layout["iy0"], layout["iy1"]
    for vk in ("front", "top", "sec", "iso"):
        vx, vy = layout[f"{vk}_x"], layout[f"{vk}_y"]
        if not (ix0 + margin_m <= vx <= ix1 - margin_m and iy0 + margin_m <= vy <= iy1 - margin_m):
            raise ValueError(f"View '{vk}' center ({vx}, {vy}) out of safe inner frame [{ix0},{ix1}]x[{iy0},{iy1}]")
    if layout["bom_y"] < iy0 + GB_TITLE_BLOCK_H_M - 1e-6 or layout["bom_x"] > ix1 + 1e-6:
        raise ValueError(f"BOM anchor ({layout['bom_x']}, {layout['bom_y']}) overlaps title block or exits frame")


def com_helper_snippet() -> str:
    """pywin32 安全调用辅助：区分带 _oleobj_ 的 CDispatch 对象与真正的方法包装器。"""
    return '''
def com(obj, name, *args):
    val = getattr(obj, name)
    if callable(val) and not hasattr(val, "_oleobj_"):
        return val(*args)
    return val
'''


def parse_preflight_output(raw: str) -> dict:
    info = {"lock_exists": False, "lock_owner": "", "license_service": "Unknown"}
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("LOCK="):
            info["lock_exists"] = line.split("=", 1)[1].lower() == "true"
        elif line.startswith("LOCK_OWNER="):
            info["lock_owner"] = line.split("=", 1)[1]
        elif line.startswith("LIC_SVC="):
            info["license_service"] = line.split("=", 1)[1]
    reasons = []
    if info["lock_exists"]:
        reasons.append(f"远端写入锁被占用 ({info['lock_owner']})，按多会话约定停手让道")
    if info["license_service"] not in ("Running", "NotInstalled"):
        reasons.append(
            f"Windows 许可服务 SolidWorks Flexnet Server 当前为 {info['license_service']}（非 Running），启动 SLDWORKS 会卡在桌面模态框"
        )
    info["ok"] = len(reasons) == 0
    info["reasons"] = reasons
    return info


def run_preflight(ssh_host: str) -> dict:
    ps = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$lock = Join-Path $env:USERPROFILE ".solidworks-cad-writer.lock"
$hasLock = Test-Path $lock
Write-Output ("LOCK=" + $hasLock)
if ($hasLock) {
  Write-Output ("LOCK_OWNER=" + ((Get-Content $lock -Raw) -replace "\r|\n"," "))
}
$svc = Get-Service "SolidWorks Flexnet Server" -ErrorAction SilentlyContinue
if ($svc) { Write-Output ("LIC_SVC=" + $svc.Status) } else { Write-Output "LIC_SVC=NotInstalled" }
"""
    enc = base64.b64encode(ps.encode("utf-16le")).decode()
    try:
        r = subprocess.run(
            ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", ssh_host,
             "powershell -NoProfile -EncodedCommand " + enc],
            capture_output=True, timeout=20,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "reasons": [f"SSH 预检超时或连接异常: {exc}"]}
    if r.returncode != 0:
        return {"ok": False, "reasons": [r.stderr.decode("utf-8", errors="replace").strip()]}
    return parse_preflight_output(r.stdout.decode("utf-8", errors="replace"))


def build_sw_worker_code(
    remote_rel_dir: str,
    part_specs: list[dict],
    assy_name: str,
    sheet_size: str = "A2",
    sw_year: int = 2025,
) -> str:
    """生成供 Solidworks-MCP `execute_python` 执行的代码（采用 SOLIDWORKS 2025 真机验证的晚绑定 COM 契约）。"""
    sheet_key = sheet_size.lower()
    lay = compute_sheet_layout_m(sheet_key)
    scale_num = 1
    specs_json = json.dumps(part_specs, ensure_ascii=False)

    return rf'''
import json, os, pythoncom, win32com.client
from pathlib import Path
{com_helper_snippet()}

NULL_DISP = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
EMPTY_VAR = win32com.client.VARIANT(pythoncom.VT_EMPTY, None)

def save_doc(doc_obj, out_file: str) -> int:
    p = Path(out_file)
    if p.exists():
        p.unlink()
    # 真机实测：Extension.SaveAs 第 4 参传 None 会触发 (-2147352571, '类型不匹配。')；统一用 IModelDoc2::SaveAs3 并核验磁盘落盘
    rc = com(doc_obj, "SaveAs3", str(p), 0, 1)
    if not p.is_file() or p.stat().st_size == 0:
        raise RuntimeError(f"[ERROR] SaveAs3 failed for {{out_file}} (rc={{rc}})")
    return p.stat().st_size

def force_view_scale(vw, num: float, den: float) -> None:
    try:
        vw.UseSheetScale = False
    except Exception:
        pass
    try:
        vw.ScaleDecimal = float(num) / float(den)
    except Exception:
        pass
    try:
        vw.ScaleRatio = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [float(num), float(den)])
    except Exception:
        pass

sw_app = globals().get("sw")
if sw_app is None:
    pythoncom.CoInitialize()
    sw_app = win32com.client.Dispatch("SldWorks.Application")

prog_data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
prog_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
tpl_dir = prog_data / "SOLIDWORKS" / "SOLIDWORKS {sw_year}" / "templates"
tpl_asm = str(tpl_dir / "gb_assembly.asmdot")
tpl_drw = str(tpl_dir / "gb_{sheet_key}.drwdot")
tpl_bom = str(prog_files / "SOLIDWORKS Corp" / "SOLIDWORKS" / "lang" / "chinese-simplified" / "gb-bom-material.sldbomtbt")

stage = Path(os.environ.get("SW_STAGE_OVERRIDE", str(Path.home() / {remote_rel_dir!r})))
specs = json.loads({specs_json!r})

# 1. 导入每个 STEP 为原生 SLDPRT 并写入国标明细栏自定义属性
prt_paths = []
for sp in specs:
    step_file = str(stage / sp["step"])
    prt_file = str(stage / (Path(sp["step"]).stem + ".SLDPRT"))
    errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    imp_data = com(sw_app, "GetImportFileData", step_file)
    pdoc = com(sw_app, "LoadFile4", step_file, "r", imp_data, errs)
    if pdoc is None:
        raise RuntimeError(f"[ERROR] LoadFile4 failed for {{step_file}}: err={{errs.value}}")
    ext = com(pdoc, "Extension")
    cpm = com(ext, "CustomPropertyManager", "")
    for k, v in (("代号", sp["code"]), ("图号", sp["code"]), ("PartNo", sp["code"]),
                 ("Description", sp["cn_name"]), ("名称", sp["cn_name"]),
                 ("Material", sp["material"]), ("材料", sp["material"]),
                 ("备注", sp.get("remark", ""))):
        com(cpm, "Add3", k, 30, v, 2)
    save_doc(pdoc, prt_file)
    com(sw_app, "CloseDoc", str(com(pdoc, "GetTitle")))
    prt_paths.append((prt_file, sp))

# 2. 基于 gb_assembly.asmdot 创建装配体，用 VT_DISPATCH 空指针调 comp.Select4 选中固定并断言 IsFixed
asm_doc = com(sw_app, "NewDocument", tpl_asm, 0, 0, 0)
if asm_doc is None:
    raise RuntimeError("[ERROR] Failed to create assembly from " + tpl_asm)
asm_title = str(com(asm_doc, "GetTitle"))
math_util = com(sw_app, "GetMathUtility")
identity_arr = [1.0,0.0,0.0, 0.0,1.0,0.0, 0.0,0.0,1.0, 0.0,0.0,0.0, 1.0, 0.0,0.0,0.0]

for prt_file, sp in prt_paths:
    o_err = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    o_warn = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    com(sw_app, "OpenDoc6", prt_file, 1, 1, "", o_err, o_warn)
    com(sw_app, "ActivateDoc3", asm_title, True, 0, o_err)
    comp = com(asm_doc, "AddComponent5", prt_file, 0, "", False, "", 0.0, 0.0, 0.0)
    if comp is None:
        raise RuntimeError(f"[ERROR] AddComponent5 returned None for {{prt_file}}")
    comp.Transform2 = com(math_util, "CreateTransform", identity_arr)
    if not com(comp, "Select4", False, NULL_DISP, False):
        raise RuntimeError(f"[ERROR] comp.Select4 failed for {{com(comp, 'Name2')}}")
    com(asm_doc, "FixComponent")
    com(asm_doc, "ClearSelection2", True)
    if not int(com(comp, "IsFixed")):
        raise RuntimeError(f"[ERROR] FixComponent did not take effect on {{com(comp, 'Name2')}}")

com(asm_doc, "EditRebuild3")
asm_path = str(stage / "{assy_name}.SLDASM")
save_doc(asm_doc, asm_path)

# 3. 基于 gb_{sheet_key}.drwdot 创建国标第一角工程图
drw = com(sw_app, "NewDocument", tpl_drw, 12, {lay['w_m']}, {lay['h_m']})
if drw is None:
    raise RuntimeError("[ERROR] Failed to create drawing from " + tpl_drw)
sheet = com(drw, "GetCurrentSheet")
props = list(com(sheet, "GetProperties2"))
if len(props) < 5 or int(round(float(props[4]))) != 1:
    raise RuntimeError(f"[ERROR] Invalid or non-First-Angle sheet properties: {{props}}")
com(sheet, "SetScale", {scale_num}, 1, False, False)

v_front = com(drw, "CreateDrawViewFromModelView3", asm_path, "*前视", {lay['front_x']}, {lay['front_y']}, 0.0)
v_top = com(drw, "CreateDrawViewFromModelView3", asm_path, "*上视", {lay['top_x']}, {lay['top_y']}, 0.0)
v_iso = com(drw, "CreateDrawViewFromModelView3", asm_path, "*等轴测", {lay['iso_x']}, {lay['iso_y']}, 0.0)
if v_front is None or v_top is None or v_iso is None:
    raise RuntimeError("[ERROR] Failed to create standard drawing views")
force_view_scale(v_iso, 4.0, 5.0)
com(v_iso, "SetDisplayMode3", False, 2, False, True)

# 在主视图中心画竖直剖切线，向右水平对齐生成 A-A 全剖左视图（第 6 参传 VT_EMPTY）
com(drw, "ActivateView", com(v_front, "GetName2"))
sk_seg = com(com(drw, "SketchManager"), "CreateLine", 0.0, -0.030, 0.0, 0.0, 0.030, 0.0)
if sk_seg is None or not com(sk_seg, "Select4", False, NULL_DISP):
    raise RuntimeError("[ERROR] Failed to create/select section cut line")
v_sec = com(drw, "CreateSectionViewAt5", {lay['sec_x']}, {lay['sec_y']}, 0.0, "A", 0, EMPTY_VAR, 0.0)
if v_sec is None:
    raise RuntimeError("[ERROR] CreateSectionViewAt5 returned None")

# 显式清空选择并按 DRAWINGVIEW 选中目标剖视图，防止气球误挂到最后创建的 v_iso 上
com(drw, "ClearSelection2", True)
com(com(drw, "Extension"), "SelectByID2", com(v_sec, "GetName2"), "DRAWINGVIEW", 0.0, 0.0, 0.0, False, 0, NULL_DISP, 0)
com(drw, "ActivateView", com(v_sec, "GetName2"))
ab_opts = com(drw, "CreateAutoBalloonOptions")
ab_opts.Layout = 3  # swDetailingBalloonLayout_Top 水平队列
ab_opts.IgnoreMultiple = True
balloons = com(drw, "AutoBalloon5", ab_opts)
if balloons is None:
    raise RuntimeError("[ERROR] AutoBalloon5 returned None")

bom_ann = com(v_sec, "InsertBomTable4", False, {lay['bom_x']}, {lay['bom_y']}, 4, 1, "", tpl_bom, False, 0, False)
if bom_ann is None:
    raise RuntimeError("[ERROR] InsertBomTable4 returned None")
# 国标明细栏 8 列总宽精确锁为 180.0 mm（与 GB/T 10609.1 标题栏左边框严丝合缝共线）
for ci, cw in enumerate([0.010, 0.032, 0.042, 0.010, 0.034, 0.014, 0.014, 0.024]):
    com(bom_ann, "SetColumnWidth", ci, cw, 0)

com(drw, "EditRebuild3")
drw_path = str(stage / "{assy_name}_GB_{sheet_size.upper()}.SLDDRW")
pdf_path = str(stage / "{assy_name}_GB_{sheet_size.upper()}.pdf")
save_doc(drw, drw_path)
save_doc(drw, pdf_path)
print(json.dumps({{"ok": True, "asm": asm_path, "drw": drw_path, "pdf": pdf_path, "bom_anchor_m": [{lay['bom_x']}, {lay['bom_y']}]}}, ensure_ascii=False))
'''


def self_test() -> None:
    import types

    # 1. CDispatch (_oleobj_) vs 方法包装器区分
    ns: dict = {}
    exec(com_helper_snippet(), ns)
    com_fn = ns["com"]

    class FakeDispatch:
        _oleobj_ = object()
        def __call__(self):
            raise AssertionError("CDispatch must not be called as method")

    class FakeOwner:
        sub_obj = FakeDispatch()
        def method_call(self, v):
            return v * 2

    owner = FakeOwner()
    assert isinstance(com_fn(owner, "sub_obj"), FakeDispatch)
    assert com_fn(owner, "method_call", 21) == 42

    # 2. 预检门禁 5 组独立正反样本
    assert not parse_preflight_output("LOCK=True\nLOCK_OWNER=other\nLIC_SVC=Running\n")["ok"]
    assert not parse_preflight_output("LOCK=False\nLIC_SVC=Stopped\n")["ok"]
    assert not parse_preflight_output("LOCK=False\nLIC_SVC=Unknown\n")["ok"]
    assert parse_preflight_output("LOCK=False\nLIC_SVC=Running\n")["ok"]
    assert parse_preflight_output("LOCK=False\nLIC_SVC=NotInstalled\n")["ok"]

    # 3. 动态内框坐标校验：A2/A3/A4 正例全过，越界坐标（如 A3 上 x=0.495m）必败报错
    for sz in ("A2", "A3", "A4"):
        lay = compute_sheet_layout_m(sz)
        assert lay["iso_x"] < lay["ix1"] - 0.015
    bad_lay = dict(compute_sheet_layout_m("A3"), iso_x=0.495)
    try:
        validate_layout_bounds(bad_lay)
    except ValueError as e:
        assert "iso" in str(e)
    else:
        raise AssertionError("Failed to reject out-of-frame iso_x=0.495 on A3")

    # 4. Mock SOLIDWORKS 全流程执行（含真机晚绑定契约校验：SaveAs3 落盘、VT_DISPATCH 空指针、DRAWINGVIEW 显式选中、180mm 齐边列宽 + 必败反例）
    import os
    import tempfile

    fake_pycom = types.ModuleType("pythoncom")
    fake_pycom.VT_BYREF = 0x4000
    fake_pycom.VT_I4 = 3
    fake_pycom.VT_DISPATCH = 9
    fake_pycom.VT_EMPTY = 0
    fake_pycom.VT_ARRAY = 0x2000
    fake_pycom.VT_R8 = 5
    fake_pycom.CoInitialize = lambda: None

    fake_w32 = types.ModuleType("win32com")
    fake_w32_client = types.ModuleType("win32com.client")

    class FakeVariant:
        def __init__(self, vt, val):
            self.vt = vt
            self.value = val

    fake_w32_client.VARIANT = FakeVariant
    fake_w32.client = fake_w32_client

    def make_mock_sw(props_override=None, fix_works: bool = True, sec_ok: bool = True, save_writes_disk: bool = True):
        calls_log: list = []
        doc_size = {"w": 0.594, "h": 0.420}

        class MockCpm:
            _oleobj_ = object()
            def Add3(self, k, t, v, opt):
                return 0

        class MockBom:
            _oleobj_ = object()
            def __init__(self):
                self.widths = {}
            def SetColumnWidth(self, ci, cw, flags):
                self.widths[ci] = cw
                calls_log.append(("col_w", ci, cw))
                return True

        class MockExt:
            _oleobj_ = object()
            def CustomPropertyManager(self, cfg):
                return MockCpm()
            def SaveAs(self, path, ver, opts, exp, err, warn):
                if exp is None:
                    raise RuntimeError("(-2147352571, '类型不匹配。', None, 4)")
                return True
            def SelectByID2(self, name, sel_type, x, y, z, append, mark, callout, sel_opt):
                assert isinstance(callout, FakeVariant) and callout.vt == fake_pycom.VT_DISPATCH
                calls_log.append(("select_id", name, sel_type))
                return True

        class MockComp:
            _oleobj_ = object()
            Name2 = "base-1"
            Transform2 = None
            _fixed = 0
            def Select4(self, append, data, popup):
                assert isinstance(data, FakeVariant) and data.vt == fake_pycom.VT_DISPATCH
                return True
            def IsFixed(self):
                return self._fixed

        class MockView:
            _oleobj_ = object()
            UseSheetScale = True
            ScaleDecimal = 1.0
            ScaleRatio = None
            def __init__(self, nm):
                self._nm = nm
            def GetName2(self):
                return self._nm
            def SetDisplayMode3(self, *a):
                return True
            def InsertBomTable4(self, use_anchor, x, y, anchor, btype, cfg, tpl, hidden, ind, follow):
                calls_log.append(("bom", use_anchor, x, y, anchor, tpl))
                return MockBom()

        class MockSheet:
            _oleobj_ = object()
            def GetProperties2(self):
                if props_override is not None:
                    return props_override
                return [12.0, 12.0, 1.0, 1.0, 1.0, doc_size["w"], doc_size["h"]]
            def SetScale(self, n, d, a, b):
                return True

        class MockSeg:
            _oleobj_ = object()
            def Select4(self, append, data):
                assert isinstance(data, FakeVariant) and data.vt == fake_pycom.VT_DISPATCH
                return True

        class MockSkMgr:
            _oleobj_ = object()
            def CreateLine(self, *a):
                return MockSeg()

        class MockDoc:
            _oleobj_ = object()
            def __init__(self, title):
                self._title = title
                self.Extension = MockExt()
                self.SketchManager = MockSkMgr()
                self._last_comp = None
            def GetTitle(self):
                return self._title
            def SaveAs3(self, path, ver, opts):
                calls_log.append(("save3", path))
                if save_writes_disk:
                    p = Path(path)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(b"SW2025_MOCK")
                return 0
            def AddComponent5(self, *a):
                self._last_comp = MockComp()
                return self._last_comp
            def FixComponent(self):
                if fix_works and self._last_comp is not None:
                    self._last_comp._fixed = 1
            def ClearSelection2(self, all_flag):
                calls_log.append(("clear_sel", bool(all_flag)))
            def EditRebuild3(self):
                return True
            def GetCurrentSheet(self):
                return MockSheet()
            def _assert_inside(self, x, y):
                c_m = 0.010 if doc_size["w"] >= 0.59 else 0.005
                assert 0.025 < x < doc_size["w"] - c_m and c_m < y < doc_size["h"] - c_m, f"Out of sheet: ({x},{y}) vs {doc_size}"
            def CreateDrawViewFromModelView3(self, path, mv, x, y, z):
                self._assert_inside(x, y)
                calls_log.append(("view", mv, x, y))
                return MockView(mv)
            def ActivateView(self, nm):
                calls_log.append(("activate_view", nm))
                return True
            def CreateSectionViewAt5(self, x, y, z, label, opts, excl, depth):
                self._assert_inside(x, y)
                assert isinstance(excl, FakeVariant) and excl.vt == fake_pycom.VT_EMPTY
                calls_log.append(("section", x, y))
                return MockView("SectionA") if sec_ok else None
            def CreateAutoBalloonOptions(self):
                return types.SimpleNamespace(Layout=0, IgnoreMultiple=False)
            def AutoBalloon5(self, opts):
                calls_log.append(("autoballoon", opts.Layout))
                return [object()]

        class MockMath:
            _oleobj_ = object()
            def CreateTransform(self, arr):
                return arr

        class MockSw:
            _oleobj_ = object()
            def GetImportFileData(self, f):
                return object()
            def LoadFile4(self, f, arg, imp, errs):
                return MockDoc("part1")
            def CloseDoc(self, t):
                pass
            def NewDocument(self, tpl, ps, w, h):
                if tpl.endswith(".drwdot"):
                    doc_size["w"], doc_size["h"] = w, h
                    return MockDoc("drw")
                return MockDoc("asm")
            def GetMathUtility(self):
                return MockMath()
            def OpenDoc6(self, *a):
                return MockDoc("part1")
            def ActivateDoc3(self, *a):
                return MockDoc("asm")

        return MockSw(), calls_log

    code_a2 = build_sw_worker_code(
        "cad/runs/demo_gb",
        [{"step": "base.step", "code": "DEMO-01", "cn_name": "底座", "material": "ABS"}],
        "DemoAssy",
        "A2",
    )
    code_a3 = build_sw_worker_code(
        "cad/runs/demo_gb",
        [{"step": "base.step", "code": "DEMO-01", "cn_name": "底座", "material": "ABS"}],
        "DemoAssy",
        "A3",
    )

    old_mods = {k: sys.modules.get(k) for k in ("pythoncom", "win32com", "win32com.client")}
    old_stage = os.environ.get("SW_STAGE_OVERRIDE")
    sys.modules["pythoncom"] = fake_pycom
    sys.modules["win32com"] = fake_w32
    sys.modules["win32com.client"] = fake_w32_client
    try:
        with tempfile.TemporaryDirectory() as tmp_stage:
            os.environ["SW_STAGE_OVERRIDE"] = tmp_stage
            sw_ok, log_a2 = make_mock_sw()
            exec(code_a2, {"sw": sw_ok})
            bom_a2 = [c for c in log_a2 if c[0] == "bom"][0]
            assert bom_a2[2] == 0.584 and bom_a2[3] == 0.066
            col_sum = round(sum(c[2] for c in log_a2 if c[0] == "col_w"), 4)
            assert col_sum == 0.180, f"Expected 180mm BOM width, got {col_sum}"
            assert ("select_id", "SectionA", "DRAWINGVIEW") in log_a2

            sw_ok3, log_a3 = make_mock_sw()
            exec(code_a3, {"sw": sw_ok3})
            bom_a3 = [c for c in log_a3 if c[0] == "bom"][0]
            assert bom_a3[2] == 0.415 and bom_a3[3] == 0.061

            # 必败 1：第三角模板拦截
            for bad_props in ([12.0, 12.0, 1.0, 1.0, 0.0, 0.594, 0.420], [12.0, 12.0]):
                sw_bad_p, _ = make_mock_sw(props_override=bad_props)
                try:
                    exec(code_a2, {"sw": sw_bad_p})
                except RuntimeError as e:
                    assert "First-Angle" in str(e)
                else:
                    raise AssertionError(f"Failed to reject bad sheet props: {bad_props}")

            # 必败 2：FixComponent 未生效拦截
            sw_bad_fix, _ = make_mock_sw(fix_works=False)
            try:
                exec(code_a2, {"sw": sw_bad_fix})
            except RuntimeError as e:
                assert "FixComponent" in str(e)
            else:
                raise AssertionError("Failed to catch unfixed component")

            # 必败 3：CreateSectionViewAt5 失败拦截
            sw_bad_sec, _ = make_mock_sw(sec_ok=False)
            try:
                exec(code_a2, {"sw": sw_bad_sec})
            except RuntimeError as e:
                assert "CreateSectionViewAt5" in str(e)
            else:
                raise AssertionError("Failed to catch failed section view")

            # 必败 4：SaveAs3 未落盘（0 字节/未生成）拦截
            sw_bad_save, _ = make_mock_sw(save_writes_disk=False)
            try:
                exec(code_a2, {"sw": sw_bad_save})
            except RuntimeError as e:
                assert "SaveAs3 failed" in str(e)
            else:
                raise AssertionError("Failed to catch empty/missing SaveAs3 output")
    finally:
        if old_stage is None:
            os.environ.pop("SW_STAGE_OVERRIDE", None)
        else:
            os.environ["SW_STAGE_OVERRIDE"] = old_stage
        for k, v in old_mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    print("PASS: sw_gb_assembly_drawing --self-test (SaveAs3, VT_DISPATCH/VT_EMPTY, DRAWINGVIEW select, 180mm BOM + 5 negative gates)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--preflight-only", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--spec", type=Path, help="JSON 文件：含 assy_name, sheet_size, remote_rel_dir, parts[]")
    ap.add_argument("--emit-calls", type=Path, help="输出供 solidworks_remote.py 调用的 JSON 文件")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return

    if args.spec and args.emit_calls:
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        code = build_sw_worker_code(
            spec.get("remote_rel_dir", "cad/runs/gb_drw"),
            spec["parts"],
            spec.get("assy_name", "Assembly"),
            spec.get("sheet_size", "A2"),
        )
        calls = [
            {"name": "connect_solidworks", "arguments": {}},
            {"name": "execute_python", "arguments": {"code": code}},
        ]
        args.emit_calls.parent.mkdir(parents=True, exist_ok=True)
        args.emit_calls.write_text(json.dumps(calls, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Wrote:", args.emit_calls)
        if not args.preflight_only:
            return

    if not args.preflight_only:
        ap.error("Specify --self-test, --preflight-only, or --spec/--emit-calls")

    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    pf = run_preflight(cfg["ssh_host"])
    print(json.dumps(pf, ensure_ascii=False, indent=2))
    sys.exit(0 if pf["ok"] else 2)


if __name__ == "__main__":
    main()
