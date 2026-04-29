import os
import time
from db_engine import check_video_exists, insert_video_record
from scraper_engine import download_video
from analyzer_engine import analyze_video_quality_and_features


def process_single_video(video_url, platform="Douyin", keyword="Manual_Input"):
    """
    处理单个视频的完整流水线：去重 -> 下载 -> 大模型解析 -> 质量过滤 -> 入库
    返回 (bool, msg) 表示是否成功和提示信息。
    """
    print(f"\n========== 开始处理手动提交的视频: {video_url} ==========")

    # 1. 去重
    if check_video_exists(video_url):
        return False, f"视频链接已存在于数据库中，跳过解析：{video_url}"

    # 2. 下载（含重试）
    video_title = f"Manual_{int(time.time())}"
    local_path = download_video(video_url, video_title, keyword, platform)

    if not local_path or not os.path.exists(local_path):
        return False, f"视频下载失败，请检查链接是否有效或受风控限制：{video_url}"

    # 3. 大模型解析
    analysis_result = analyze_video_quality_and_features(local_path)

    if not analysis_result:
        return False, "大模型解析视频失败或返回了无效的数据。"

    # 4. 质量过滤
    is_high_quality   = analysis_result.get("is_high_quality", False)
    low_quality_reason = analysis_result.get("low_quality_reason", "")

    if not is_high_quality:
        if os.path.exists(local_path):
            os.remove(local_path)
        return False, f"视频质量判定为不优质，拦截入库。理由：{low_quality_reason}"

    # 5. 入库
    db_record = {
        "platform":       platform,
        "search_keyword": keyword,
        "video_title":    video_title,
        "video_link":     video_url,
        "local_path":     local_path,
        **analysis_result,
    }

    inserted = insert_video_record(db_record)
    if inserted:
        return True, "视频成功解析并入库！"
    else:
        return False, "视频解析成功，但写入数据库时发生冲突。"
