#!/usr/bin/env python3
"""
后台运行 SeedAnce 批量生成（由 app.py 通过 subprocess.Popen 调用）。
Usage: python3 run_seedance_bg.py --video_path VAL --script_json VAL
"""
import sys, os, json, argparse

_PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PROJ)

from creator_engine import split_video_segments, run_seedance_batch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video_path",  required=True, help="本地视频路径")
    parser.add_argument("--script_json", required=True, help="Gemini生成的脚本 JSON 文件路径")
    args = parser.parse_args()

    if not os.path.exists(args.video_path):
        print(f"[BG] ✗ 视频文件不存在: {args.video_path}", flush=True)
        sys.exit(1)

    with open(args.script_json, encoding="utf-8") as f:
        script = json.load(f)

    print(f"[BG] ① 开始切片: {os.path.basename(args.video_path)}", flush=True)
    segments = split_video_segments(args.video_path, segment_duration=15)
    print(f"[BG] ① 切片完成: 共 {len(segments)} 段", flush=True)
    for i, p in enumerate(segments, 1):
        size_mb = os.path.getsize(p) / 1024 / 1024
        print(f"[BG]    段 {i}: {os.path.basename(p)} ({size_mb:.1f}MB)", flush=True)

    print(f"[BG] ② 开始 SeedAnce 批量生成...", flush=True)
    results = run_seedance_batch(segments, script)

    ok   = sum(1 for r in results if "output_path" in r)
    fail = sum(1 for r in results if "error" in r)
    print(f"[BG] ③ 全部完成: 成功 {ok}/{len(results)}, 失败 {fail}", flush=True)

    for r in results:
        if "output_path" in r:
            print(f"[BG] ✓ 段 {r['segment']}: {r['output_path']}", flush=True)
        else:
            print(f"[BG] ✗ 段 {r['segment']}: {r.get('error','unknown')}", flush=True)

    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
