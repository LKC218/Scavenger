"""检查交付文件、报告链接和离线依赖，不访问网络。"""

import json
import math
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "reports/RESTAND玩法交互研究.html"


class ReportParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.links = []
        self.dependencies = []
        self.languages = []
        self.images = []

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if "id" in attr:
            self.ids.append(attr["id"])
        if tag == "html":
            self.languages.append(attr.get("lang"))
        if tag == "a" and "href" in attr:
            self.links.append(attr["href"])
        if tag == "img":
            self.images.append({"src": attr.get("src") or "", "alt": attr.get("alt") or ""})
        if tag in {"script", "img", "iframe", "video", "audio", "source"} and attr.get("src"):
            self.dependencies.append(attr["src"])
        if tag == "link" and attr.get("rel") == "stylesheet":
            self.dependencies.append(attr["href"])


def read_json(path, errors):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        errors.append(f"无法读取 JSON：{path.relative_to(ROOT)}（{error}）")
        return None


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def image_path(url, base):
    if not isinstance(url, str):
        return None
    target = urlsplit(url)
    if target.scheme or target.netloc or not target.path:
        return None
    return (base / unquote(target.path)).resolve()


def check_review(record, videos, parser, errors):
    if not isinstance(record, dict):
        errors.append("全片审阅记录须为对象")
        return
    if record.get("视频网址") not in [item.get("网址") for item in videos]:
        errors.append("全片审阅视频网址未登记为视频来源")
    decode = record.get("解码", {})
    if not isinstance(decode, dict):
        errors.append("全片审阅记录缺少解码对象")
        return
    expected = decode.get("容器帧数")
    decoded = decode.get("顺序解码帧数")
    for label, value in [("容器帧数", expected), ("顺序解码帧数", decoded)]:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            errors.append(f"解码记录的{label}须为正整数")
    if expected != decoded:
        errors.append("顺序解码帧数与容器帧数不一致")
    duration = decode.get("播放时长秒")
    final_frame = decode.get("末帧秒")
    if not is_number(duration) or duration <= 0:
        errors.append("解码记录缺少有效播放时长")
        return
    if not is_number(final_frame) or not 0 <= final_frame <= duration or duration - final_frame > 1:
        errors.append("解码末帧未覆盖全片尾部，或时间超出播放时长")

    timeline = record.get("全片时间线")
    if not isinstance(timeline, list) or not timeline:
        errors.append("全片审阅记录缺少全片时间线")
    else:
        previous_end = 0.0
        for index, segment in enumerate(timeline, 1):
            if not isinstance(segment, dict):
                errors.append(f"全片时间线第 {index} 段须为对象")
                continue
            start, end = segment.get("开始秒"), segment.get("结束秒")
            if not is_number(start) or not is_number(end) or not 0 <= start < end <= duration + 0.001:
                errors.append(f"全片时间线第 {index} 段区间无效")
                continue
            if abs(start - previous_end) > 0.001:
                errors.append(f"全片时间线第 {index} 段与前段不连续或未从零开始")
            previous_end = end
            for field in ["标题", "观察", "限制"]:
                if not segment.get(field):
                    errors.append(f"全片时间线第 {index} 段缺少{field}")
        if abs(previous_end - duration) > 0.001:
            errors.append("全片时间线未覆盖播放时长末尾")

    sequences = record.get("连续复查区间")
    if not isinstance(sequences, list) or not sequences:
        errors.append("全片审阅记录缺少连续复查区间")
    else:
        for index, sequence in enumerate(sequences, 1):
            if not isinstance(sequence, dict):
                errors.append(f"连续复查第 {index} 项须为对象")
                continue
            start, end, fps = sequence.get("开始秒"), sequence.get("结束秒"), sequence.get("帧率")
            if not is_number(start) or not is_number(end) or not 0 <= start < end <= duration:
                errors.append(f"连续复查第 {index} 项区间无效")
            if not is_number(fps) or fps <= 0:
                errors.append(f"连续复查第 {index} 项帧率无效")
            if not sequence.get("结果"):
                errors.append(f"连续复查第 {index} 项缺少结果")

    images = record.get("报告图片")
    if not isinstance(images, list) or not images:
        errors.append("全片审阅记录缺少报告图片")
        return
    page_images = {image_path(item["src"], PAGE.parent) for item in parser.images}
    recorded = set()
    for index, item in enumerate(images, 1):
        if not isinstance(item, dict):
            errors.append(f"报告图片第 {index} 项须为对象")
            continue
        path = image_path(item.get("路径", ""), ROOT)
        if path is None or not path.is_relative_to(ROOT / "reports/assets/实机") or not path.is_file():
            errors.append(f"报告图片第 {index} 项文件不存在或不在实机目录")
        elif path not in page_images:
            errors.append(f"归档实机图片未在报告引用：{item['路径']}")
        if path in recorded:
            errors.append(f"报告图片记录重复：{item.get('路径')}")
        recorded.add(path)
        timestamp = item.get("时码秒")
        if not is_number(timestamp) or not 0 <= timestamp <= duration:
            errors.append(f"报告图片第 {index} 项时间码无效")
        for field in ["说明", "来源"]:
            if not item.get(field):
                errors.append(f"报告图片第 {index} 项缺少{field}")
    for path in page_images:
        if path is not None and path.is_relative_to(ROOT / "reports/assets/实机") and path not in recorded:
            errors.append(f"报告实机图片缺少审阅记录：{path.relative_to(ROOT)}")


def main():
    required = [
        "README.md", ".gitignore", "docs/apps-code-map.md",
        "docs/项目结构与开发约定.md", "docs/项目开发经验/项目开发经验.md",
        "reports/RESTAND玩法交互研究.html", "reports/报告维护说明.md",
        "reports/assets/报告.css", "reports/assets/报告.js",
        "research/RESTAND/来源台账.json", "research/RESTAND/观察记录.md",
        "research/RESTAND/研究方法与证据说明.md", "research/RESTAND/全片审阅记录.json",
        "scripts/预览报告.py", "scripts/审阅公开视频.py",
    ]
    errors = [f"缺少文件：{path}" for path in required if not (ROOT / path).is_file()]
    if not PAGE.is_file():
        print("\n".join(errors))
        return 1
    body = PAGE.read_text(encoding="utf-8")
    parser = ReportParser()
    parser.feed(body)
    if parser.languages != ["zh-CN"]:
        errors.append("报告须声明 zh-CN")
    if len(set(parser.ids)) != len(parser.ids):
        errors.append("报告含重复锚点")
    for item in parser.images:
        if not item["src"] or not item["alt"].strip():
            errors.append(f"报告图片缺少 src 或非空 alt：{item['src'] or '未填写路径'}")
    for url in parser.links + parser.dependencies:
        target = urlsplit(url)
        if target.scheme or target.netloc:
            if url in parser.dependencies:
                errors.append(f"离线正文依赖远程资源：{url}")
            continue
        if target.path:
            dest = (PAGE.parent / unquote(target.path)).resolve()
            if not dest.is_relative_to(ROOT) or not dest.exists():
                errors.append(f"本地链接不存在或超出项目：{url}")
        elif target.fragment and unquote(target.fragment) not in parser.ids:
            errors.append(f"锚点不存在：{url}")
    ledger = read_json(ROOT / "research/RESTAND/来源台账.json", errors)
    sources = ledger.get("来源", []) if isinstance(ledger, dict) else []
    if not sources:
        errors.append("来源台账缺少来源")
    codes = [item["编号"] for item in sources]
    if len(codes) != len(set(codes)):
        errors.append("来源编号重复")
    for source in sources:
        if source["网址"] not in parser.links:
            errors.append(f"报告未引用来源：{source['编号']}")
    videos = [s for s in sources if s["类型"] == "视频"]
    if not videos:
        errors.append("来源台账未登记视频")
    for video in videos:
        if video["标题"] not in body or video["网址"] not in parser.links:
            errors.append(f"报告视频标题或网址与台账不一致：{video['编号']}")
    review = read_json(ROOT / "research/RESTAND/全片审阅记录.json", errors)
    check_review(review, videos, parser, errors)
    for marker in ["本项目建议", "主题待定", "非游戏截图"]:
        if marker not in body:
            errors.append(f"缺少研究范围说明：{marker}")
    if errors:
        print("检查未通过：\n" + "\n".join(f"- {item}" for item in errors))
        return 1
    print(f"检查通过：{len(required)} 项文件、{len(parser.links)} 条链接、{len(sources)} 条来源、{len(videos)} 条视频、{len(parser.images)} 张报告图片及全片审阅记录。")
    print("本次检查不访问外部链接，不代表浏览器交互或游戏性能通过验收。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
