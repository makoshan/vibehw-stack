#!/usr/bin/env python3
"""下载 fccid.io 上某个 FCC 附件（内部照片等）的 PDF，校验、逐页转图、拼印样、写来源说明。

用法：fcc_photos.py <fccid.io 附件页 URL 或 直链> [--out DIR] [--sha256 HEX]
  例：fcc_photos.py https://fccid.io/2AGOZ-WQ8/Internal-Photos/IntPho-8483380 --out docs/ref-x
依赖：poppler 的 pdftoppm；ImageMagick 的 montage（没有就跳过印样）。
不伪装浏览器：用诚实的 User-Agent；站点对页面返回 403 时直接报错，不绕过（页面交给内置浏览器读）。
"""
import argparse, hashlib, re, shutil, subprocess, sys, urllib.request, urllib.error
from pathlib import Path

UA = "fcc-teardown/0.1 (personal research)"  # 诚实标识，不冒充浏览器

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404: sys.exit(f"404：{url}\n附件不存在，核对 FCC ID 与附件编号（可能保密或编号抄错）。")
        sys.exit(f"HTTP {e.code}：{url}\n站点拒绝脚本访问。不要伪装请求头；改用内置浏览器打开该页面，从页面上取附件。")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url"); ap.add_argument("--out", default="."); ap.add_argument("--sha256")
    a = ap.parse_args()
    m = re.match(r"https://fccid\.io/([0-9A-Za-z]+-[0-9A-Za-z]+)/([\w-]+)/([\w.-]+?)-(\d+)(\.pdf)?$", a.url)
    if not m: sys.exit("只认 https://fccid.io/<FCCID>/<类型>/<名>-<数字id> 形式的附件链接")
    fccid, kind, name, doc = m.group(1), m.group(2), m.group(3), m.group(4)
    pdf_url = a.url if a.url.endswith(".pdf") else a.url + ".pdf"
    data = fetch(pdf_url)
    if not data.startswith(b"%PDF"): sys.exit("返回的不是 PDF，没有保存")  # 防止把 403/广告页当成果
    sha = hashlib.sha256(data).hexdigest()
    if a.sha256 and a.sha256.lower() != sha: sys.exit(f"SHA-256 不符：期望 {a.sha256}，实际 {sha}")
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    pdf = out / f"{name}-{doc}.pdf"; pdf.write_bytes(data)
    if not shutil.which("pdftoppm"): sys.exit("缺 pdftoppm（brew install poppler）；PDF 已保存：" + str(pdf))
    pages = out / "pages"; pages.mkdir(exist_ok=True)
    subprocess.run(["pdftoppm", "-r", "90", "-png", str(pdf), str(pages / "p")], check=True)
    pngs = sorted(pages.glob("p-*.png"))
    sheets = []
    if shutil.which("montage"):
        for i in range(0, len(pngs), 9):  # 每张印样 3×3
            s = out / f"contact-sheet-{i // 9 + 1}.png"
            subprocess.run(["montage", *map(str, pngs[i:i + 9]), "-tile", "3x3", "-geometry", "520x+4+4", str(s)],
                           stderr=subprocess.DEVNULL)  # 找不到字体时 montage 报 1 但图已生成，所以不用退出码，改查文件
            if s.exists() and s.stat().st_size > 0: sheets.append(s.name)
    (out / "README.md").write_text(
        f"# {fccid} {kind}\n\n- 来源：{a.url}\n- 文件：{pdf.name}，{len(data)} 字节，{len(pngs)} 页，SHA-256 `{sha}`\n"
        f"- 印样：{', '.join(sheets) or '无（缺 montage）'}；逐页图在 pages/\n"
        f"- 版权属申请人，仅作内部参考，不对外转载。\n", encoding="utf-8")
    print(f"ok fccid={fccid} pages={len(pngs)} sha256={sha} out={out}")

if __name__ == "__main__":
    main()
