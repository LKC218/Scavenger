"""获取公开可播放的视频，顺序解码并生成带时间码的审阅联系表。"""

import argparse
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def format_timestamp(seconds):
    # 先取整到百分之一秒，再拆分分钟，避免浮点数显示为01:60.00。
    centiseconds = round(seconds * 100)
    minutes, remainder = divmod(centiseconds, 6000)
    whole_seconds, fraction = divmod(remainder, 100)
    return f"{minutes:02}:{whole_seconds:02}.{fraction:02}"


def public_json(url, referer):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": referer})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def download_bilibili(bvid, output):
    if output.exists():
        raise FileExistsError('视频输出已存在，请使用新的任务路径，避免覆盖研究原件。')
    referer = f"https://www.bilibili.com/video/{bvid}/"
    meta = public_json("https://api.bilibili.com/x/web-interface/view?" + urlencode({"bvid": bvid}), referer)
    if meta.get("code") != 0:
        raise RuntimeError("公开元数据不可用，停止获取。")
    data = meta["data"]
    play = public_json("https://api.bilibili.com/x/player/playurl?" + urlencode({"bvid": bvid, "cid": data["cid"], "qn": 64, "fnval": 0}), referer)
    if play.get("code") != 0:
        raise RuntimeError("公开播放接口未授权播放，停止获取。")
    media = play["data"]
    if media.get("is_preview") or len(media.get("durl", [])) != 1:
        raise RuntimeError("仅有预览或当前脚本不支持的分段格式，停止获取。")
    if abs(media.get("timelength", 0) / 1000 - data["duration"]) > 2:
        raise RuntimeError("公开播放时长与源时长不一致，停止获取。")
    output.parent.mkdir(parents=True, exist_ok=True)
    request = Request(media["durl"][0]["url"], headers={"User-Agent": "Mozilla/5.0", "Referer": referer})
    digest = hashlib.sha256()
    with urlopen(request, timeout=60) as response, output.open("wb") as destination:
        while chunk := response.read(1024 * 1024):
            destination.write(chunk)
            digest.update(chunk)
    manifest = {"来源": referer, "标题": data["title"], "作者": data["owner"]["name"], "页面时长秒": data["duration"], "播放时长毫秒": media["timelength"], "清晰度代码": media["quality"], "文件字节": output.stat().st_size, "SHA256": digest.hexdigest(), "获取方式": "标准公开播放接口，无登录、无用户 Cookie；不保存临时媒体签名网址"}
    output.with_suffix(".json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False), flush=True)


def contact_sheets(video, directory, interval, batch_seconds, start=0, end=None):
    import cv2
    import numpy as np

    directory.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError("无法解码本地视频。")
    fps = capture.get(cv2.CAP_PROP_FPS)
    expected = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    columns, width, height, label = 5, 384, 216, 22
    frames_per_sheet = max(1, round(batch_seconds / interval))
    if start:
        capture.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    tiles, index, next_sample, batch = [], 0, start, 0
    sampled = []

    def save_sheet():
        rows = math.ceil(len(tiles) / columns)
        sheet = np.full((rows * (height + label), columns * width, 3), 22, dtype=np.uint8)
        for tile_index, (timestamp, frame) in enumerate(tiles):
            x = (tile_index % columns) * width
            y = (tile_index // columns) * (height + label)
            sheet[y:y + height, x:x + width] = cv2.resize(frame, (width, height))
            text = format_timestamp(timestamp)
            cv2.putText(sheet, text, (x + 7, y + height + 15), cv2.FONT_HERSHEY_SIMPLEX, .45, (205, 230, 210), 1, cv2.LINE_AA)
        path = directory / f"联系表-{batch:02}.jpg"
        cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tofile(str(path))

    while True:
        ok, frame = capture.read()
        if not ok:
            break
        timestamp = start + index / fps
        if end is not None and timestamp > end:
            break
        if timestamp + 0.0001 >= next_sample:
            tiles.append((timestamp, frame.copy()))
            sampled.append(round(timestamp, 3))
            next_sample += interval
            if len(tiles) >= frames_per_sheet:
                save_sheet()
                tiles.clear()
                batch += 1
        index += 1
    if tiles:
        save_sheet()
        batch += 1
    capture.release()
    result = {"视频": video.name, "帧率": fps, "容器帧数": expected, "顺序解码帧数": index, "开始秒": start, "结束限制秒": end, "末帧秒": start + (index - 1) / fps if index else 0, "审阅联系表间隔秒": interval, "联系表数量": batch, "选帧数量": len(sampled), "说明": "指定范围内顺序解码，联系表按时间顺序保留；选帧间隔和实际人工观察范围必须另行说明，不等于逐帧观看。"}
    (directory / "解码记录.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)


def subtitle_timeline(video, directory, interval):
    """连续解码、识别画面内字幕；识别结果仍需人工交叉审阅。"""
    import cv2
    from rapidocr_onnxruntime import RapidOCR

    directory.mkdir(parents=True, exist_ok=True)
    engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError("无法解码本地视频。")
    fps = capture.get(cv2.CAP_PROP_FPS)
    expected = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    index, next_sample, rows = 0, 0.0, []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        timestamp = index / fps
        if timestamp + .0001 >= next_sample:
            height, width = frame.shape[:2]
            crop = frame[int(height * .9):int(height * .995), int(width * .15):int(width * .9)]
            found, _ = engine(crop)
            texts = [item[1] for item in found or [] if item[2] >= .65]
            text = " ".join(texts)
            if text:
                if rows and rows[-1]["text"] == text and timestamp - rows[-1]["end"] < interval * 2.1:
                    rows[-1]["end"] = round(timestamp, 3)
                else:
                    rows.append({"start": round(timestamp, 3), "end": round(timestamp, 3), "text": text})
            next_sample += interval
        index += 1
    capture.release()
    result = {"video": video.name, "fps": fps, "expected_frames": expected,
              "decoded_frames": index, "interval_seconds": interval, "rows": rows,
              "limit": "完整顺序解码；字幕OCR有误识别和短字幕遗漏，不等于音频转写或逐帧人工观看。关键操作另查连续帧。"}
    (directory / "字幕识别.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "字幕识别.txt").write_text("\n".join(f"{format_timestamp(r['start'])}–{format_timestamp(r['end'])} {r['text']}" for r in rows), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--下载", action="store_true", help="获取用户指定的公开 B站视频")
    parser.add_argument("--bvid", default="BV1Kix469EpF")
    parser.add_argument("--文件", type=Path, default=ROOT / "tmp/RESTAND/战斗实机.mp4")
    parser.add_argument("--间隔", type=float, default=1.0)
    parser.add_argument("--分段", type=float, default=20.0)
    parser.add_argument("--输出", type=Path, default=ROOT / "tmp/RESTAND/战斗联系表")
    parser.add_argument("--字幕", action="store_true", help="连续解码并识别画面底部字幕，不生成联系表")
    parser.add_argument("--开始", type=float, default=0)
    parser.add_argument("--结束", type=float)
    args = parser.parse_args()
    if args.间隔 <= 0 or args.分段 <= 0:
        parser.error("间隔和分段必须为正数。")
    if args.开始 < 0 or (args.结束 is not None and args.结束 <= args.开始):
        parser.error("片段范围无效。")
    if args.字幕 and (args.开始 or args.结束 is not None):
        parser.error("字幕模式处理全文件，片段范围仅用于联系表。")
    if args.下载:
        download_bilibili(args.bvid, args.文件)
    if args.字幕:
        subtitle_timeline(args.文件, args.输出, args.间隔)
    else:
        contact_sheets(args.文件, args.输出, args.间隔, args.分段, args.开始, args.结束)


if __name__ == "__main__":
    main()
