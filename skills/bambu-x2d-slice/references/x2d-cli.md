# X2D CLI：2026-09-24 实测案例

适用环境：macOS，Bambu Studio 02.08.02.61，X2D 双 0.4 mm Standard 喷嘴。其他版本先核对 `--help` 和官方预设。

## 最小执行形状

在独立 ASCII 临时目录放入已展平的 machine.json、process.json、PETG-HF.json、PLA-Lite.json 和独立 STL。工作目录、输入与 outputdir 都用这个目录。下面文件名为示例，按实际件数替换：

```sh
"/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"   --debug 2 --datadir "$PWD/data"   --load-settings "$PWD/machine.json;$PWD/process.json"   --load-filaments "$PWD/PETG-HF.json;$PWD/PLA-Lite.json"   --orient 0 --arrange 1 --slice 0   --export-3mf PETG-PLA-Interface.3mf --outputdir "$PWD"   "$PWD/upper_shell_print.stl" "$PWD/bottom_ring_print.stl"   "$PWD/xiao_tray_print.stl" "$PWD/xiao_cap_print.stl" > slice.log 2>&1
```

`--export-3mf` 使用文件名，不传绝对路径：此版本会把绝对路径再次拼到 outputdir。用 Python subprocess 参数数组执行时无需 shell 转义。成功后复制到用户项目目录。

预设：`Bambu Lab X2D 0.4 nozzle`、`0.16mm Standard @BBL X2D`、`Bambu PETG HF @BBL X2D 0.4 nozzle`、`Bambu PLA Lite @BBL X2D 0.4 nozzle`。递归展开继承和 include 后才用于 CLI，不能只加载很短的叶 JSON。

## 本次覆盖值：EMG V04 四件，非通用默认

- 纹理 PEI；0.16 mm 层高；4 壁、20% gyroid；outer_only brim 4 mm、间隙0.15 mm。
- `enable_support=1`，`support_type=tree(auto)`，`support_on_build_plate_only=1`。
- `support_filament=1`（PETG 主体/树干），`support_interface_filament=2`（PLA）。
- 顶/底接触 Z 距离0，`support_interface_spacing=0`，顶部界面3层；物理拆除效果未验收。
- 输入 `filament_map=[1,2]`；实际导出逻辑挤出机1/2。CLI 将 `filament_map_mode` 归一成 Auto For Flush，`filament_map_2`/`filament_nozzle_map` 为0/1；不能假设输入 Manual 生效，也不能混淆不同字段的0/1基数。
- `flush_volumes_matrix=[0,800,800,0]`，`flush_volumes_vector=[140,140,140,140]`。800为这次保守候选，不是校准结果；双喷嘴文件实际分开用料，不据此声称单喷嘴混料已验证。两种预览色分别橙/白，仅为显示。
- `enable_prime_tower=1`，宽35 mm，`wipe_tower_x=[35]`、`wipe_tower_y=[180]`。需随新排盘复查碰撞；X2D第二逻辑喷嘴官方有效X下限20.5 mm，默认塔X15会越界。

## 已遇问题及处理

1. 绝对 export 路径重复拼接、中文路径导出失败：本轮用 ASCII 临时目录＋文件名导出成功；不要泛化成所有 Unicode 不支持。
2. 导入先前生成的 layout.3mf 再覆盖耗材曾崩溃：改为原始独立 STL 输入成功。
3. 双料缺少冲刷矩阵时在 FilamentGroup 崩溃：补齐2×2矩阵及向量后不再崩溃，不等于证明所有崩溃都由此引起。
4. 随后退出154/return_code -102：结果明确报多喷嘴不可打印区域；擦料塔移到共同范围后退出0。禁止绕过边界检查。
5. 日志出现 Invalid T command T65279/T65535，两命令确在官方 X2D 结束 G-code 中，原样保留。切片整体退出0，但实际设备执行未验收；新版本/其他代码报错不能照搬忽略。
6. CLI `slice_info.config` 中 `printer_model_id` 为空，项目设置仍是 X2D。设备发送兼容性未验证；应在 Studio 作为项目打开核对，不称为存储卡直打已验收。

## 本次验证证据

项目根目录 `~/go/202608/智能手表`，`models/emg-wrist-v04-x2d/` 包含 config、日志、PETG-PLA-validation.json 与双料 3mf；`evidence/2026-09-24-emg-v04-x2d-slice.md` 为证据。路径仅用于本机回查，本 Skill 的使用不依赖该项目存在。

ZIP CRC通过；4独立对象；137层；PLA用于层索引97–99的界面及擦料塔，未用于墙、填充或树干。估算4010秒，PETG20.29g、PLA0.32g。上壳顶面贴板、内腔向上，支撑用于悬空表耳。模型几何未修改，未发送打印，实物未验收。
