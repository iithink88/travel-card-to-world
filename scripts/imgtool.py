#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
travel-card-to-world / imgtool.py

子命令:
  prepare <图...>            预处理: EXIF方向矫正 + sRGB + 限长边 + 输出干净 JPG
  crop    <图> --ratio 4:5   居中裁切到指定比例
  sheet   <目录或图...>      多版结果拼成一张对比图（带序号）

示例:
  python imgtool.py prepare D:/photo/IMG_0001.HEIC --max 2048 --info
  python imgtool.py crop out/result.png --ratio 4:5 --out out/result_45.jpg
  python imgtool.py sheet out/ --out out/compare.jpg --height 700
"""
import argparse
import json
import os
import sys
from glob import glob

try:
    from PIL import Image, ImageDraw, ImageFont, ImageStat, ImageOps
except ImportError:
    sys.exit("需要 Pillow：pip install pillow")

SUPPORTED = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif")


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def to_srgb_rgb(im, icc_profile=None):
    """统一到 sRGB 的 RGB 图。"""
    if icc_profile is not None:
        try:
            from PIL import ImageCms
            src = ImageCms.ImageCmsProfile(icc_profile)
            dst = ImageCms.createProfile("sRGB")
            im = ImageCms.profileToProfile(im, src, dst, outputMode="RGB")
        except Exception:
            pass
    if im.mode != "RGB":
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
    return im


def dominant_colors(im, k=3):
    small = im.resize((48, 48), Image.LANCZOS).convert("RGB")
    q = small.quantize(colors=8, method=Image.Quantize.FASTOCTREE)
    pal = q.getpalette() or []
    counts = sorted(q.getcolors() or [], reverse=True)
    out = []
    for cnt, idx in counts[:k]:
        r, g, b = pal[idx * 3: idx * 3 + 3]
        out.append({"hex": "#%02X%02X%02X" % (r, g, b), "share": round(cnt / (48 * 48), 3)})
    return out


def info_of(im):
    w, h = im.size
    g = im.convert("L")
    st = ImageStat.Stat(g)
    from math import gcd
    d = gcd(w, h) or 1
    return {
        "size": [w, h],
        "aspect": f"{w // d}:{h // d}",
        "ratio_wh": round(w / h, 4),
        "orientation": "竖版" if h > w else ("横版" if w > h else "方图"),
        "brightness": round(st.mean[0] / 255, 3),
        "contrast": round(st.stddev[0] / 128, 3),
        "dominant": dominant_colors(im),
    }


def cmd_prepare(args):
    paths = []
    for p in args.files:
        if os.path.isdir(p):
            paths += [f for f in glob(os.path.join(p, "*")) if f.lower().endswith(SUPPORTED)]
        else:
            paths.append(p)
    if not paths:
        sys.exit("没有找到可处理的图片")

    report = []
    for p in paths:
        if not os.path.isfile(p):
            print(f"[跳过] 不存在: {p}", file=sys.stderr)
            continue
        try:
            im = Image.open(p)
            im.load()
        except Exception as e:
            print(f"[跳过] 无法读取 {p}: {e}", file=sys.stderr)
            continue

        icc = im.info.get("icc_profile")
        im = ImageOps.exif_transpose(im) or im
        im = to_srgb_rgb(im, icc)

        w, h = im.size
        if max(w, h) > args.max:
            s = args.max / max(w, h)
            im = im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)

        if args.out and len(paths) == 1:
            out = args.out
        else:
            d = os.path.dirname(os.path.abspath(p))
            stem = os.path.splitext(os.path.basename(p))[0]
            out = os.path.join(d, f"{stem}_clean.jpg")
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        im.save(out, "JPEG", quality=args.quality, optimize=True, progressive=True)

        rec = {"input": p, "output": out, "bytes": os.path.getsize(out),
               "bytes_human": human(os.path.getsize(out)), **info_of(im)}
        report.append(rec)
        print(f"[OK] {out}  {rec['size'][0]}x{rec['size'][1]}  {rec['bytes_human']}")

    if args.info:
        print("\n--- INFO ---")
        print(json.dumps(report if len(report) > 1 else (report[0] if report else {}),
                         ensure_ascii=False, indent=2))


def parse_ratio(s):
    if ":" not in s:
        sys.exit(f"比例格式错误: {s}（应形如 4:5）")
    a, b = s.split(":")
    return float(a) / float(b)


def cmd_crop(args):
    im = Image.open(args.file)
    im = ImageOps.exif_transpose(im) or im
    im = to_srgb_rgb(im, im.info.get("icc_profile"))
    target = parse_ratio(args.ratio)
    w, h = im.size
    if abs(w / h - target) < 0.005:
        print(f"[无需裁切] 已是 {args.ratio}")
        out_im = im
    else:
        if w / h > target:          # 太宽 → 裁左右
            nw = int(round(h * target))
            x = (w - nw) // 2
            box = (x, 0, x + nw, h)
        else:                        # 太高 → 裁上下
            nh = int(round(w / target))
            y = int((h - nh) * args.anchor)
            box = (0, y, w, y + nh)
        out_im = im.crop(box)
        print(f"[裁切] {w}x{h} -> {out_im.size[0]}x{out_im.size[1]}  ratio={args.ratio}")

    out = args.out or os.path.splitext(args.file)[0] + "_45.jpg"
    out_im.save(out, "JPEG", quality=args.quality, optimize=True, progressive=True)
    print(f"[OK] {out}")


def load_font(size):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "msyh.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def cmd_sheet(args):
    paths = []
    for p in args.items:
        if os.path.isdir(p):
            paths += sorted(f for f in glob(os.path.join(p, "*"))
                            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")))
        else:
            paths.append(p)
    if not paths:
        sys.exit("没有找到图片")
    if args.limit:
        paths = paths[:args.limit]

    H = args.height
    gap = args.gap
    pad = 16
    thumbs = []
    for p in paths:
        try:
            im = Image.open(p)
            im.load()
            im = ImageOps.exif_transpose(im) or im
            im = to_srgb_rgb(im, im.info.get("icc_profile"))
        except Exception as e:
            print(f"[跳过] {p}: {e}", file=sys.stderr)
            continue
        s = H / im.size[1]
        thumbs.append((p, im.resize((max(1, int(im.size[0] * s)), H), Image.LANCZOS)))

    if not thumbs:
        sys.exit("没有可用图片")

    cols = args.cols or len(thumbs)
    rows = (len(thumbs) + cols - 1) // cols
    cw = max(t[1].size[0] for t in thumbs)
    W = pad * 2 + cols * cw + (cols - 1) * gap
    Hh = pad * 2 + rows * (H + 26) + (rows - 1) * gap

    sheet = Image.new("RGB", (W, Hh), (246, 246, 244))
    draw = ImageDraw.Draw(sheet)
    font = load_font(16)

    for i, (p, t) in enumerate(thumbs):
        r, c = divmod(i, cols)
        x = pad + c * (cw + gap) + (cw - t.size[0]) // 2
        y = pad + r * (H + 26 + gap)
        sheet.paste(t, (x, y))
        draw.text((pad + c * (cw + gap), y + H + 4), f"#{i + 1}", fill=(70, 70, 70), font=font)

    out = args.out or os.path.join(os.path.dirname(os.path.abspath(paths[0])) or ".", "compare.jpg")
    sheet.save(out, "JPEG", quality=92, optimize=True, progressive=True)
    print(f"[OK] {out}  {sheet.size[0]}x{sheet.size[1]}  ({len(thumbs)} 张)")


def main():
    ap = argparse.ArgumentParser(description="travel-card-to-world 图像工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("prepare", help="预处理为可上传的干净 JPG")
    p1.add_argument("files", nargs="+")
    p1.add_argument("--out")
    p1.add_argument("--max", type=int, default=2048, help="长边上限，默认 2048")
    p1.add_argument("--quality", type=int, default=92)
    p1.add_argument("--info", action="store_true", help="输出尺寸/比例/亮度/主色 JSON")
    p1.set_defaults(func=cmd_prepare)

    p2 = sub.add_parser("crop", help="居中裁切到指定比例")
    p2.add_argument("file")
    p2.add_argument("--ratio", default="4:5")
    p2.add_argument("--out")
    p2.add_argument("--quality", type=int, default=93)
    p2.add_argument("--anchor", type=float, default=0.5,
                    help="裁上下时的垂直锚点，0=顶 0.5=居中 1=底，默认 0.5")
    p2.set_defaults(func=cmd_crop)

    p3 = sub.add_parser("sheet", help="多版结果拼对比图")
    p3.add_argument("items", nargs="+")
    p3.add_argument("--out")
    p3.add_argument("--height", type=int, default=700)
    p3.add_argument("--cols", type=int, default=0)
    p3.add_argument("--gap", type=int, default=12)
    p3.add_argument("--limit", type=int, default=0)
    p3.set_defaults(func=cmd_sheet)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
