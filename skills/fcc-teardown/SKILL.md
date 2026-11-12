---
name: fcc-teardown
version: 0.2.0
description: >
  查电子产品的内部结构照片：从 FCC 设备授权库（经 fccid.io）取「内部照片 Internal Photos」PDF，
  校验 SHA-256、逐页转图、拼印样、写来源说明。能拿到整板正反面、屏蔽罩、电池、天线位置；
  拿不到芯片级细节、原理图、BOM（申请人多申请长期保密，页面只显示 Metadata only）。
  Use when: 用户说「拆解图」「内部照片」「这个产品里面长什么样」「FCC 内部照片」「查某某手环/手表/耳机内部结构」，
  或要给硬件设计找真机内部参考（电池、天线、主板怎么叠）。
  不管渲染图（没有现成来源）；不管 iFixit 拆解文章（直接搜）；不管版权转载。
---

# FCC 内部照片查询

**先说限制**：产品要先申请 FCC 认证才有照片；新品的内部照片常申请短期保密，最长约 180 天后才公开。
照片是测试实验室为合规拍的，整板居多，不是为拆解拍的。

## 流程（P0：产品名 → 内部照片 PDF → 图）

1. **找 FCC ID**：没有现成接口，靠搜索，规则如下（2026-11-09 实测）：
   - 优先级：用户给的 ID > 设备机身或包装、说明书、E-label 上的 ID > 搜索。
   - 搜索用 WebSearch，`allowed_domains: ["fccid.io"]`，关键词用「品牌 + **型号号码** + FCC ID」，不要用营销名。
     ID 形如「授权码-型号」，授权码是公司固定前缀（Apple=BCG，Meta=2AGOZ），已知型号就能直接拼。
   - **命中判据**：结果标题或页面里同时出现品牌（Grantee）和型号才算；只是同类产品（如一堆「Smart Ring」）不算命中。
   - 查不到就直说「没查到」，请用户给 ID 或型号；不要挑最像的一个。同一公司下有多个新型号时，列出候选让用户选。
   - 实测：`Apple Watch A1891 FCC ID` → 命中 BCG-A1891；`Meta Neural Band …` 只给营销名 → 搜不到 WQ8；编造的「Zorblax QX-9000」→ 只返回无关智能戒指，应判为未命中。
2. **读附件页**：fccid.io 的 HTML 页面对脚本返回 403，**不要伪装请求头去绕**（2026-11-09 实测被拦）。
   用内置浏览器（`mcp__Claude_Browser__*`）打开 `https://fccid.io/<FCCID去掉连字符>`（如 `2AGOZWQ8`），
   `get_page_text` 看 Exhibits 列表，再用 `javascript_tool` 取链接：
   `[...document.querySelectorAll('a')].filter(a=>/IntPho|Internal/.test(a.textContent)).map(a=>a.href)`。
   - 列表里没有 Internal Photos，或「Short-Term Confidentiality: Yes」且日期在今天之后 → 还没公开，直说，别猜。
   - 标 Metadata only 的附件（原理图、BOM、框图）拿不到，不要找绕路。
3. **下载并拼图**：
   `python3 -I ~/.claude/skills/fcc-teardown/scripts/fcc_photos.py <附件页 URL> --out <目录> [--sha256 <页面上的哈希>]`
   PDF 直链（页面 URL 加 `.pdf`）用诚实 User-Agent 可下，脚本校验是 PDF、对哈希、转图、出印样和 README。
4. **看图**：用 Read 看 `contact-sheet-*.png`；要细看某页读 `pages/p-NN.png`。

## 汇报格式
按页说明拍了什么；分「能看出 / 看不出」两栏；推断的写明是推断（例如把照片里的凸起说成电极）。
型号对不上产品时（如 WQ8 是否为 Meta Neural Band）标「未验证」。

## 已验证（2026-11-09）
- WQ8（2AGOZ-WQ8）内部照片：17 页，SHA-256 与页面一致；阳性复现印样字节相同；错误哈希退出 1；不存在的编号报 404 退出 1。
- Apple Watch A1891（BCG-A1891）：10 页，同样跑通。
- 脚本只认 `https://fccid.io/<FCCID>/<类型>/<名>-<数字id>`。

## 未做
- 只给营销名时转 FCC ID（要靠型号号码，上面已写规则）；批量查询（要走官方接口或限速，fccid.io 会 403）；渲染图；iFixit 合并。
- 版权属申请人：只放内部参考目录，不做公开网站，除非先查转载权限。
