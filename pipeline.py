import os
import time
from db_engine import check_video_exists, insert_video_record
from scraper_engine import scrape_top_videos, download_video
from analyzer_engine import analyze_video_quality_and_features


def load_keywords(platform):
    """从配置文件加载关键词列表，忽略空行和 # 注释"""
    file_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "config",
        f"keywords_{platform.lower()}.txt",
    )
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def run_pipeline_for_platform(platform, top_n=5):
    print(f"\n{'=' * 60}")
    print(f"  {platform} 自动化爬取与解析流水线启动  (top_n={top_n})")
    print(f"{'=' * 60}")

    keywords = load_keywords(platform)
    if not keywords:
        print(f"[-] {platform} 没有配置任何关键词，跳过。")
        return

    total_success = total_skip = total_fail = 0

    for keyword in keywords:
        print(f"\n>>> 关键词: 「{keyword}」")

        videos = scrape_top_videos(platform, keyword, top_n)
        if not videos:
            print(f"    [-] 未搜索到任何视频，跳过此关键词。")
            continue

        for video_info in videos:
            video_link  = video_info.get("url")
            video_title = video_info.get("title", keyword)
            view_count  = video_info.get("view_count", 0)
            like_count  = video_info.get("like_count", 0)
            duration    = video_info.get("duration", 0)

            print(f"\n  标题: {video_title[:50]}")
            print(f"  链接: {video_link}")

            # 1. 去重
            if check_video_exists(video_link):
                print(f"    [跳过] 已在库中")
                total_skip += 1
                continue

            # 2. 下载（含重试）
            local_path = download_video(video_link, video_title, keyword, platform)
            if not local_path or not os.path.exists(local_path):
                print(f"    [-] 下载失败，跳过")
                total_fail += 1
                continue

            # 3. 大模型分析
            analysis_result = analyze_video_quality_and_features(local_path)
            if not analysis_result:
                print(f"    [-] 解析失败，跳过")
                total_fail += 1
                continue

            # 4. 质量过滤
            is_high_quality = analysis_result.get("is_high_quality", False)
            if not is_high_quality:
                reason = analysis_result.get("low_quality_reason", "")
                print(f"    [拦截] 低质量: {reason}")
                try:
                    os.remove(local_path)
                except OSError:
                    pass
                total_fail += 1
                continue

            # 5. 入库（携带搜索元数据 + AI 分析结果）
            db_record = {
                "platform":       platform,
                "search_keyword": keyword,
                "video_title":    video_title,
                "video_link":     video_link,
                "local_path":     local_path,
                "view_count":     view_count,
                "like_count":     like_count,
                "duration_seconds": duration,
                **analysis_result,
            }

            inserted = insert_video_record(db_record)
            if inserted:
                print(f"    [✓] 入库成功！")
                total_success += 1
            else:
                print(f"    [-] 入库冲突（并发重复），跳过")
                total_fail += 1

            time.sleep(2)   # 避免频繁请求

    print(f"\n[{platform}] 完成 — 成功: {total_success}  跳过: {total_skip}  失败: {total_fail}")


def main():
    # 支持通过环境变量控制（由 app.py 的 subprocess 传入）
    top_n    = int(os.environ.get("TOP_N", 5))
    platform = os.environ.get("PLATFORM", "抖音 + TikTok")

    if "抖音" in platform or "Douyin" in platform or "+" in platform:
        run_pipeline_for_platform("Douyin", top_n=top_n)
    if "TikTok" in platform or "+" in platform:
        run_pipeline_for_platform("TikTok", top_n=top_n)

    print("\n========== 所有平台任务执行完毕 ==========")


if __name__ == "__main__":
    main()
