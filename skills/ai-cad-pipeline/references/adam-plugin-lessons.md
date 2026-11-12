# Adam – AI Copilot（Fusion 插件）架构拆解与可迁移做法

读的是本机已安装的 bundle：`~/Library/Application Support/Autodesk/ApplicationPlugins/Adam_AI_Copilot.bundle`
（入口 `Contents/AdamFusion.py` 是明文，handlers/bridge 是 .pyc，用 marshal 取 co_consts 拿到结构与字符串）。
日期 2026-09-03。下面只记**能搬到我们工作流**的东西，不记它的产品实现。

## 1. 它把「模型操作」切成五个动词，而不是「写脚本」

`handlers/` 下就是五个 handler，每个有一个 `handle` 入口：

| 动词 | 内部函数 | 说明 |
|---|---|---|
| `read` | `_q_volume _q_area _q_length _q_edges _q_centroid _q_similar _q_faces _q_bodies _q_selection`，另有 feature / materialAppearance | 只读查询，按 `queryType` 分发 |
| `create` | `_box _cylinder _sphere _torus _coil _add_brep_body _project_onto_plane _perpendicular_to _construction_plane_from_normal` | 图元 + 构造几何 |
| `update` | `_undo _redo _run_history_command _execute_command_definition _can_history _command_enabled _user_parameter _model_parameter _parameter_update` | **改参数**、跑历史命令、撤销 |
| `delete` | `_delete_by_names _delete_all_of_type _delete_timeline_object _checked_delete` | 按名/按类型/按时间线项 |
| `execute` | `_script` + `_document _document_open _document_close _document_save _find_data_file_by_id` | 任意脚本**只是其中一支**，另有文档生命周期 |

**要点**：任意脚本是逃生舱，不是主路径。绝大多数意图都有带类型、可校验、可单独失败的入口。
我们这边正相反——什么都靠「改 .step.py 再整体重生成」，所以一个数字的改动也要跑完整条链，
而且我这轮两次因为字符串拼接注释把文件写出 SyntaxError。

## 2. 只读查询是带类型的，缺参数就明确报错

从 `read.pyc` 里拿到的错误串：

```
Unknown queryType:
'area' query requires entityToken
'length' query requires entityToken=edge
'centroid' query requires entityToken
'similar' query requires entityToken
bodyToken does not resolve to a BRepBody
'feature' query requires featureType
'materialAppearance' requires entityTokens or entityToken
```

**要点**：查询答不上来就说缺哪个参数，绝不静默返回空。
这正是我们踩过的坑——退化的布尔切片返回空集，探针于是「通过」，实际什么都没量到
（本轮 `sec.vertices()` 量前脸平面宽度返回 0mm 就是这个）。

## 3. 实体 token 压缩

`_common.pyc` 里有 `compress_token / decompress_token / _int_to_token / _token_to_int / _trim_entity_tokens`。
Fusion 的 entity token 是很长的 base64 串；它映射成短整数再进 LLM 上下文。

**要点**：给模型看的引用要短且稳定。我们的 `inspect refs --facts` 输出冗长，
装配里 `o1.1..o1.15` 已经是这个思路，但探针脚本里到处是长表达式。

## 4. 能力守卫（capability guard）

`_can_history` / `_command_enabled` —— 先问「这个操作现在能不能做」，再做。

**要点**：等价于我们该在提交前先验证布尔会成功（`BRepAlgoAPI_Check`），
而不是做完再发现实体非法、然后回滚整轮。变半径圆角那几轮就是这么浪费的。

## 5. 选择监听器

`bridge/selection_watcher.pyc`：监听 `ui.activeSelectionChanged`，把用户当前选中的东西推给 AI（事件名 `selection.changed`）。
用户说「这个面」时 AI 知道是哪个。

**要点**：我们没有等价物，但对应的做法是——让用户能用**渲染图上的位置**指认特征，
所以出图时保留可指认的标注（正交视图 + 网格）比出漂亮渲染更有用。

## 6. 页面看门狗

`bridge/watchdog.pyc`：后台心跳 `bridge.ping`，页面不应答就重建面板
（`Adam watchdog: page unresponsive — rebuilding palette`）。且是「armed 之后才判」——旧版页面不 pong 就永不触发，避免误杀。

**要点**：本轮 Fusion MCP 适配器假死两次（进程活、端口 LISTEN、任何请求被 reset），
我第一次干等了 10 分钟。应当：**先探活再发长任务，探活失败立刻报告用户重启 Fusion，不要重试等待**。

## 7. UI 注册的稳健写法（如果哪天要写 Fusion 插件）

- 面板属于**单个 tab**，所以要在 Design 工作区**每个 tab** 都注册一份，panel id 由 tab id 派生（id 全局唯一）
- 另外注册到 QAT 右簇（跨工作区常驻）和 Utilities → ADD-INS（用户习惯去找的地方）
- `run()` 里先删旧定义再建，保证 Stop → Run 不留重复按钮；Fusion 每次会话都会忘记 UI 定制，所以必须每次启动都注册
- 每个 tab 单独 try/except：某个 tab 拒绝自定义面板不能连累其它 tab

## 落地：`scripts/fitprobe.py`

第 1、2 条已经实现成 `ai-cad-pipeline/scripts/fitprobe.py`——把本轮手写过五六遍的检查固化成带类型的查询：
`clash / fits / envelope / wall_at / solids / outside / sag`。
已用 lobster-box v5.0 实测，七项答案与手工结论一致（sag 0.29 对解析 0.3；69×50 不通过、差 133.1mm³ 与手工完全相同）。
