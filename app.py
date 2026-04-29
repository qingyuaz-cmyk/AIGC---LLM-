import streamlit as st
import json
import pandas as pd
import os
import time
import subprocess

# 本地开发：自动加载 .env 文件里的环境变量（Docker 环境直接用系统环境变量）
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass
from db_engine import get_all_records, check_video_exists, insert_video_record
from creator_engine import filter_db_records, get_video_duration, split_video_segments, generate_recreation_script, parse_script_json, call_seedance, run_seedance_batch
from scraper_engine import download_video
from analyzer_engine import analyze_video_quality_and_features

CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")


def _process_no_quality_filter(video_url, platform, keyword):
    """不过滤质量，强制入库（仍标注 is_high_quality 字段）"""
    if check_video_exists(video_url):
        return False, "视频链接已存在于数据库中"
    video_title = f"Manual_{int(time.time())}"
    local_path = download_video(video_url, video_title, keyword, platform)
    if not local_path or not os.path.exists(local_path):
        return False, "视频下载失败"
    analysis_result = analyze_video_quality_and_features(local_path)
    if not analysis_result:
        return False, "大模型解析失败"
    db_record = {
        "platform": platform, "search_keyword": keyword,
        "video_title": video_title, "video_link": video_url,
        "local_path": local_path, **analysis_result,
    }
    return insert_video_record(db_record), "强制入库完成"


def process_single_video_link(video_url, platform="Douyin", keyword="Manual_Input"):
    """单链接处理流水线：去重 → 下载 → 大模型解析 → 质量过滤 → 入库"""
    if check_video_exists(video_url):
        return False, f"视频链接已存在于数据库中，跳过解析：{video_url}"

    video_title = f"Manual_{int(time.time())}"
    local_path = download_video(video_url, video_title, keyword, platform)

    if not local_path or not os.path.exists(local_path):
        return False, f"视频下载失败，请检查链接是否有效或受风控限制：{video_url}"

    analysis_result = analyze_video_quality_and_features(local_path)
    if not analysis_result:
        return False, "大模型解析视频失败或返回了无效的数据。"

    is_high_quality = analysis_result.get("is_high_quality", False)
    low_quality_reason = analysis_result.get("low_quality_reason", "")

    if not is_high_quality:
        if os.path.exists(local_path):
            os.remove(local_path)
        return False, f"视频质量判定为不优质，拦截入库。理由：{low_quality_reason}"

    db_record = {
        "platform": platform,
        "search_keyword": keyword,
        "video_title": video_title,
        "video_link": video_url,
        "local_path": local_path,
        **analysis_result,
    }

    inserted = insert_video_record(db_record)
    if inserted:
        return True, "视频成功解析并入库！"
    else:
        return False, "视频解析成功，但写入数据库时发生冲突。"



_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))



def _run_seedance_streaming(video_path, script, env_overrides):
    """后台子进程运行 SeedAnce 批量切片+生成，实时日志流式显示。"""
    script_tmp = f"/tmp/seedance_script_{int(time.time())}.json"
    with open(script_tmp, "w", encoding="utf-8") as _sf:
        json.dump(script, _sf, ensure_ascii=False)
    log_path = f"/tmp/seedance_{int(time.time())}.log"
    cmd_env = os.environ.copy()
    cmd_env.update(env_overrides)
    bg_script = os.path.join(_PROJECT_DIR, "run_seedance_bg.py")
    with open(log_path, "w") as _lf:
        proc = subprocess.Popen(
            ["python3", bg_script, "--video_path", video_path, "--script_json", script_tmp],
            stdout=_lf, stderr=subprocess.STDOUT, text=True,
            env=cmd_env, cwd=_PROJECT_DIR,
        )
    st.info("SeedAnce 生成已在后台启动，实时日志如下（每秒刷新）。")
    log_box = st.empty()
    last_size = 0
    displayed_lines = []
    while proc.poll() is None:
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as _lf:
                _lf.seek(last_size)
                new_text = _lf.read()
                if new_text:
                    last_size += len(new_text.encode("utf-8"))
                    displayed_lines += new_text.splitlines()
        except Exception:
            pass
        log_box.code("\n".join(displayed_lines[-40:]), language="text")
        time.sleep(1)
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as _lf:
            displayed_lines = _lf.read().splitlines()
        log_box.code("\n".join(displayed_lines[-60:]), language="text")
    except Exception:
        pass
    if proc.returncode == 0:
        st.success("SeedAnce 全部完成！视频保存在 seedance_output/ 目录")
    else:
        st.error(f"SeedAnce 异常退出 (code={proc.returncode})，请查看日志")

def _run_pipeline_streaming(cmd_env):
    """启动 pipeline.py 子进程，实时滚动展示输出日志。"""
    log_path = f"/tmp/pipeline_{int(time.time())}.log"

    with open(log_path, "w") as log_f:
        proc = subprocess.Popen(
            ["python3", os.path.join(_PROJECT_DIR, "pipeline.py")],
            stdout=log_f,
            stderr=subprocess.STDOUT,
            text=True,
            env=cmd_env,
            cwd=_PROJECT_DIR,
        )

    st.info("流水线已启动，实时日志如下（每秒刷新）：")
    log_box = st.empty()
    stop_btn = st.empty()

    last_size = 0
    displayed_lines = []

    while proc.poll() is None:
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as lf:
                lf.seek(last_size)
                new_text = lf.read()
                if new_text:
                    last_size += len(new_text.encode("utf-8"))
                    displayed_lines += new_text.splitlines()
        except Exception:
            pass
        # Show last 40 lines
        log_box.code("\n".join(displayed_lines[-40:]), language="text")
        time.sleep(1)

    # Final flush
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as lf:
            displayed_lines = lf.read().splitlines()
        log_box.code("\n".join(displayed_lines[-60:]), language="text")
    except Exception:
        pass

    if proc.returncode == 0:
        st.success("✅ 流水线执行完毕！请刷新页面查看最新数据。")
    else:
        st.error(f"流水线异常退出（code={proc.returncode}）。请查看上方日志。")


# ─────────────────────────────────────────────
#  页面配置
# ─────────────────────────────────────────────
st.set_page_config(page_title="AIGC直播短带直二创", layout="wide")
st.title("AIGC直播短带直二创")
st.markdown("---")

# ─────────────────────────────────────────────
#  数据大盘
# ─────────────────────────────────────────────
records = get_all_records()
total_videos   = len(records)
douyin_count   = sum(1 for r in records if r.get("platform") == "Douyin")
tiktok_count   = sum(1 for r in records if r.get("platform") == "TikTok")
hq_count       = sum(1 for r in records if r.get("is_high_quality"))
hq_rate        = f"{int(hq_count / total_videos * 100)}%" if total_videos > 0 else "N/A"

st.header("📊 数据大盘")
col1, col2, col3, col4 = st.columns(4)
col1.metric("已入库视频总数", total_videos)
col2.metric("抖音视频数量",   douyin_count)
col3.metric("TikTok 视频数量", tiktok_count)
col4.metric("优质视频率",     hq_rate)

st.header("⚙️ 操作控制台")

tab1, tab2, tab3, tab4 = st.tabs(
    ["🚀 自动爬取解析top热度视频", "🔗 手动链接解析", "🍪 Cookie 配置", "🎬 主播二创生产"]
)

# ─────────────────────────────────────────────
#  Tab 1 — 自动流水线
# ─────────────────────────────────────────────
with tab1:
    st.subheader("自动爬取解析top热度视频")
    st.info(
        "配置关键词后，系统将自动搜索 → 下载 → 大模型解析 → 低质过滤 → 入库。\n"
        "首次运行前请先在【🍪 Cookie 配置】页配置登录态，否则可能被风控限制。"
    )

    st.markdown("#### 关键词管理")
    st.caption("每行一个关键词，`#` 开头的行为注释，保存后下次流水线自动生效。")

    kw_sub1, kw_sub2 = st.tabs(["抖音关键词", "TikTok 关键词"])
    for _pname, _kw_widget in [("douyin", kw_sub1), ("tiktok", kw_sub2)]:
        with _kw_widget:
            _kw_path = os.path.join(CONFIG_DIR, f"keywords_{_pname}.txt")
            _current_kw = ""
            if os.path.exists(_kw_path):
                with open(_kw_path, "r", encoding="utf-8") as f:
                    _current_kw = f.read()
            _label = "抖音" if _pname == "douyin" else "TikTok"
            _new_kw = st.text_area(
                f"{_label} 关键词列表",
                value=_current_kw,
                height=200,
                key=f"kw_{_pname}",
            )
            if st.button(f"💾 保存 {_label} 关键词", key=f"save_kw_{_pname}"):
                with open(_kw_path, "w", encoding="utf-8") as f:
                    f.write(_new_kw)
                st.success(f"{_label} 关键词已保存！")

    st.markdown("---")
    st.markdown("#### 爬取配置")
    col_a, col_b = st.columns(2)
    with col_a:
        top_n = st.slider("每个关键词抓取视频数量", min_value=1, max_value=20, value=5)
    with col_b:
        platform_choice = st.selectbox(
            "选择平台", ["抖音 + TikTok（双平台）", "仅抖音", "仅TikTok"]
        )

    if st.button("🚀 启动一键爬取分析流", type="primary", key="run_pipeline"):
        cmd_env = os.environ.copy()
        cmd_env["TOP_N"]    = str(top_n)
        cmd_env["PLATFORM"] = platform_choice
        _run_pipeline_streaming(cmd_env)

    # ── 入库数据预览 ─────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 入库数据预览")

    if total_videos == 0:
        st.info("当前数据库为空，启动流水线采集后数据将出现在此处。")
    else:
        df = pd.DataFrame(records)

        col_f1, col_f2, col_f3 = st.columns(3)
        platform_filter = col_f1.multiselect(
            "平台", ["Douyin", "TikTok"], default=["Douyin", "TikTok"], key="pf_filter"
        )
        keyword_filter = col_f2.multiselect(
            "关键词", sorted(df["search_keyword"].dropna().unique().tolist()), key="kw_filter"
        )
        region_opts = (
            sorted(df["country_region"].dropna().unique().tolist())
            if "country_region" in df.columns else []
        )
        region_filter = col_f3.multiselect("地域", region_opts, key="rg_filter")

        df_filtered = df.copy()
        if platform_filter:
            df_filtered = df_filtered[df_filtered["platform"].isin(platform_filter)]
        if keyword_filter:
            df_filtered = df_filtered[df_filtered["search_keyword"].isin(keyword_filter)]
        if region_filter:
            df_filtered = df_filtered[df_filtered["country_region"].isin(region_filter)]

        view_mode = st.radio(
            "展示维度",
            ["基础信息", "内容分析", "AIGC 提示词"],
            horizontal=True,
            key="view_mode",
        )
        if view_mode == "基础信息":
            display_cols = [
                "id", "platform", "search_keyword", "video_title", "video_link",
                "country_region", "duration_seconds", "view_count", "like_count",
                "content_type_tags", "created_at",
            ]
        elif view_mode == "内容分析":
            display_cols = [
                "id", "platform", "search_keyword",
                "content_type_tags", "style_type_tags",
                "country_region", "main_content", "core_highlights",
            ]
        else:
            display_cols = [
                "id", "platform", "search_keyword",
                "content_type_tags", "style_type_tags", "country_region",
                "key_shot_prompts_cn",
            ]

        df_show = df_filtered[[c for c in display_cols if c in df_filtered.columns]]
        st.caption(f"显示 {len(df_filtered)} / {total_videos} 条记录")
        st.dataframe(df_show, use_container_width=True, height=500)

        st.download_button(
            "📥 导出当前筛选结果 CSV",
            data=df_filtered.to_csv(index=False).encode("utf-8"),
            file_name="video_analysis_export.csv",
            mime="text/csv",
            key="csv_export",
        )

# ─────────────────────────────────────────────
#  Tab 2 — 手动批量链接解析
# ─────────────────────────────────────────────
with tab2:
    st.subheader("批量链接解析入库")
    st.markdown(
        "粘贴视频链接（每行一条），系统将逐条执行 **下载 → 大模型解析 → 质量过滤 → 入库**。\n\n"
        "支持：抖音短链 `v.douyin.com`、抖音长链 `douyin.com/video/...`、TikTok 链接。"
    )

    col_cfg1, col_cfg2, col_cfg3 = st.columns([2, 2, 1])
    with col_cfg1:
        plat_sel = st.selectbox("平台", ["Douyin", "TikTok"], key="batch_platform")
    with col_cfg2:
        batch_kw = st.text_input("分类标签（批次标记）", value="Manual_Batch",
                                 help="同一批次的链接会共用这个标签作为 search_keyword 入库")
    with col_cfg3:
        skip_low = st.checkbox("跳过低质视频", value=True,
                               help="取消勾选则低质视频也入库（仍标注 is_high_quality=false）")

    raw_urls = st.text_area(
        "视频链接列表（每行一条）",
        height=220,
        placeholder=(
            "https://v.douyin.com/xxxxxx/\n"
            "https://v.douyin.com/yyyyyy/\n"
            "https://www.tiktok.com/@user/video/1234567890\n"
            "# 以 # 开头的行会被忽略"
        ),
    )

    # 解析有效 URL 列表
    url_list = [
        line.strip()
        for line in raw_urls.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    if url_list:
        st.caption(f"共检测到 **{len(url_list)}** 条链接")
    else:
        st.caption("请在上方粘贴链接")

    if st.button("▶ 开始批量下载并解析", type="primary", key="run_batch", disabled=not url_list):
        total   = len(url_list)
        results = []   # list of (url, status, message)

        progress_bar  = st.progress(0, text="准备中…")
        status_box    = st.empty()
        log_container = st.container()

        for idx, url in enumerate(url_list, start=1):
            pct  = int((idx - 1) / total * 100)
            progress_bar.progress(pct, text=f"处理第 {idx}/{total} 条：{url[:60]}…")
            status_box.info(f"⏳ 正在处理（{idx}/{total}）：`{url}`")

            ok, msg = process_single_video_link(
                video_url=url,
                platform=plat_sel,
                keyword=batch_kw.strip(),
            )

            # 低质视频强制入库（若用户取消了 skip_low）
            if not ok and not skip_low and "不优质" in msg:
                # 重新处理：只下载+分析，不过滤质量
                ok, msg = _process_no_quality_filter(url, plat_sel, batch_kw.strip())

            results.append((url, "✅ 成功" if ok else "❌ 失败/跳过", msg))
            with log_container:
                if ok:
                    st.success(f"[{idx}/{total}] ✅ {url[:70]}")
                else:
                    st.warning(f"[{idx}/{total}] ⚠ {msg[:100]}")

        progress_bar.progress(100, text="全部处理完成！")
        status_box.empty()

        # 结果汇总表
        st.markdown("---")
        st.subheader("本批次处理结果")
        success_n = sum(1 for _, s, _ in results if "成功" in s)
        skip_n    = sum(1 for _, _, m in results if "已存在" in m)
        fail_n    = total - success_n - skip_n

        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("成功入库", success_n)
        mc2.metric("跳过（已有）", skip_n)
        mc3.metric("失败/低质拦截", fail_n)

        result_df = pd.DataFrame(results, columns=["链接", "状态", "详情"])
        st.dataframe(result_df, use_container_width=True, height=300)

        if success_n > 0:
            st.balloons()

# ─────────────────────────────────────────────
#  Tab 3 — Cookie 配置
# ─────────────────────────────────────────────
with tab3:
    st.subheader("Cookie 配置（提升搜索成功率）")
    st.warning(
        "⚠️ 搜索抖音/TikTok 需要有效的登录态 Cookie，否则大概率被风控限制，无法获取搜索结果。"
    )

    with st.expander("📖 如何获取 Cookie？（点击展开）", expanded=True):
        st.markdown("""
**方法一：浏览器扩展（最简单）**
1. Chrome 商店安装扩展：**Get cookies.txt LOCALLY**
2. 打开对应网站并**保持登录状态**
   - 抖音：https://www.douyin.com
   - TikTok：https://www.tiktok.com（需要海外IP）
3. 点击扩展图标 → 当前站点 → **Export** → 复制全部内容
4. 粘贴到下方对应平台的文本框中，点击【保存】

---

**方法二：开发者工具手动提取**
1. 浏览器按 `F12` → `Application` → `Cookies` → 选中对应域名
2. 找到关键字段，按格式手动填写：
   - **抖音关键字段**：`sessionid` / `ttwid` / `passport_csrf_token` / `odin_tt` / `s_v_web_id`
   - **TikTok关键字段**：`sessionid` / `tt_webid` / `msToken` / `tiktok_webapp_theme`
3. **Netscape Cookie 格式**（制表符分隔）：
   ```
   .douyin.com	TRUE	/	TRUE	1999999999	sessionid	your_value
   ```

---
⚠️ **注意事项**
- Cookie 通常有 **7~30 天**有效期，失效后需重新导出
- TikTok 需搭配**海外代理**使用（在 docker-compose.yml 中配置 HTTP_PROXY）
- 建议使用**小号**，避免主账号因异常登录被封
        """)

    cookie_col1, cookie_col2 = st.columns(2)

    for platform_name, col_widget in [("douyin", cookie_col1), ("tiktok", cookie_col2)]:
        with col_widget:
            label       = "抖音" if platform_name == "douyin" else "TikTok"
            cookie_path = os.path.join(CONFIG_DIR, f"cookies_{platform_name}.txt")

            current_cookie = ""
            if os.path.exists(cookie_path):
                with open(cookie_path, "r", encoding="utf-8") as f:
                    current_cookie = f.read()

            real_lines = [
                l for l in current_cookie.split("\n")
                if l.strip() and not l.startswith("#")
            ]

            st.markdown(f"**{label} Cookie**")
            if real_lines:
                st.success(f"✓ 已配置 {len(real_lines)} 条 Cookie 记录")
            else:
                st.error("✗ 尚未配置有效 Cookie")

            new_cookie = st.text_area(
                f"粘贴 {label} Cookie（Netscape 格式）",
                value=current_cookie,
                height=220,
                key=f"cookie_{platform_name}",
                placeholder=(
                    "# Netscape HTTP Cookie File\n"
                    f".{platform_name if platform_name == 'tiktok' else 'douyin'}.com"
                    "\tTRUE\t/\tTRUE\t1999999999\tsessionid\tyour_value_here"
                ),
            )

            if st.button(f"💾 保存 {label} Cookie", key=f"save_cookie_{platform_name}"):
                with open(cookie_path, "w", encoding="utf-8") as f:
                    f.write(new_cookie)
                st.success(f"{label} Cookie 已保存！流水线将在下次运行时自动加载。")

# ─────────────────────────────────────────────
#  Tab 4 — 主播二创生产
# ─────────────────────────────────────────────
with tab4:
    st.subheader("主播二创脚本生成与 SeedAnce 一键出片")
    st.markdown(
        "填写主播特征、上传抽帧画面和原始看点视频，系统将自动从热度视频库中筛选"
        "最相关的套路参考，调用 Gemini 生成统一二创方案，再对视频按 15s 分段逐一生成 SeedAnce 提示词。"
    )

    st.markdown("#### Step 1 · 主播信息输入")
    col_l, col_r = st.columns([3, 2])

    with col_l:
        st.markdown("**主播特征（必填）**")
        c1, c2, c3 = st.columns(3)
        filter_region  = c1.text_input("国家/地域 *", placeholder="如：欧美、中国大陆")
        filter_style   = c2.text_input("风格标签 *",  placeholder="如：才艺,优雅")
        filter_content = c3.text_input("内容类型 *",  placeholder="如：才艺展示,音乐表演")

        live_type = st.text_area(
            "本场直播看点内容",
            height=120,
            placeholder=(
                "例：摩洛哥主播小提琴演奏专场，主打才艺展示+变装互动，"
                "暖光居家风格，受众以欧美用户为主"
            ),
            key="live_type",
        )

    with col_r:
        frame_file = st.file_uploader("主播抽帧画面（PNG / JPG）", type=["png", "jpg", "jpeg"])
        video_file = st.file_uploader("主播原始看点视频（MP4）",   type=["mp4", "mov"])

        if frame_file:
            st.caption(f"已上传抽帧：{frame_file.name}")
        if video_file:
            st.caption(f"已上传视频：{video_file.name}")

    st.markdown("---")
    st.markdown("#### Step 2 · 生成二创方案")

    _fields_ok = all([
        filter_region.strip(), filter_style.strip(), filter_content.strip(),
        live_type.strip(), frame_file, video_file,
    ])
    if not _fields_ok:
        _missing = []
        if not filter_region.strip():  _missing.append("国家/地域")
        if not filter_style.strip():   _missing.append("风格标签")
        if not filter_content.strip(): _missing.append("内容类型")
        if not live_type.strip():      _missing.append("本场直播看点内容")
        if not frame_file:             _missing.append("主播抽帧画面")
        if not video_file:             _missing.append("主播原始看点视频")
        st.warning(f"请填写/上传以下必填项：{'、'.join(_missing)}")

    if st.button("生成二创脚本", type="primary", key="gen_script", disabled=not _fields_ok):

        # 每次点击都清空上次结果，强制重新匹配+重新调用 Gemini
        for _k in ("creator_script_raw", "creator_video_path", "creator_n_segments"):
            st.session_state.pop(_k, None)

        # 保存上传文件到 temp
        import math
        os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_creator"), exist_ok=True)
        tmp_base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_creator")

        frame_path = os.path.join(tmp_base, frame_file.name)
        with open(frame_path, "wb") as f:
            f.write(frame_file.getvalue())

        video_path = os.path.join(tmp_base, video_file.name)
        with open(video_path, "wb") as f:
            f.write(video_file.getvalue())

        # ① 筛选热度视频库
        with st.spinner("从热度视频库中筛选匹配记录…"):
            matched = filter_db_records(filter_region, filter_style, filter_content, live_type)

        st.info(f"筛选到 **{len(matched)}** 条热度视频参考（按相似度排序，取前 6 条传给模型）")

        if matched:
            with st.expander("查看匹配到的参考视频", expanded=True):
                for i, r in enumerate(matched[:6], 1):
                    score = r.get("_match_score", 0)
                    st.markdown(
                        f"**{i}.** `{r.get('country_region')}` | "
                        f"风格: {r.get('style_type_tags','—')} | "
                        f"内容: {r.get('content_type_tags','—')} | "
                        f"匹配分: **{score}**"
                    )
                    st.caption(r.get("core_highlights", ""))

        # ② 获取视频时长 → 计算分段数
        duration = get_video_duration(video_path)
        n_segments = min(4, max(1, math.ceil(duration / 15))) if duration > 0 else 1
        if duration > 60:
            st.info(f"视频时长约 **{duration:.1f}s**，超过 60s，将加速压缩后发送 Gemini，最多生成 **{n_segments}** 段（每段 15s）")
        else:
            st.info(f"视频时长约 **{duration:.1f}s**，将切分为 **{n_segments}** 段（每段 15s）")

        # ③ 调用 Gemini 生成二创脚本
        with st.spinner(f"正在调用 Gemini 生成 {n_segments} 段二创脚本，请稍候…"):
            try:
                _live_type = st.session_state.get("live_type", "")
                _profile_parts = []
                if filter_region.strip():
                    _profile_parts.append(f"【主播地域/国家】{filter_region}")
                if filter_style.strip():
                    _profile_parts.append(f"【主播风格标签】{filter_style}")
                if filter_content.strip():
                    _profile_parts.append(f"【主播内容类型】{filter_content}")
                if _live_type.strip():
                    _profile_parts.append(f"【本场直播看点内容】{_live_type}")
                _profile = "\n".join(_profile_parts)
                raw_result = generate_recreation_script(
                    creator_profile_text=_profile,
                    creator_frame_path=frame_path,
                    creator_video_path=video_path,
                    matched_records=matched,
                    total_segments=n_segments,
                )
                st.session_state["creator_script_raw"]  = raw_result
                st.session_state["creator_video_path"]  = video_path
                st.session_state["creator_n_segments"]  = n_segments
            except Exception as e:
                st.error(f"Gemini 调用失败：{e}")
                raw_result = None

        if raw_result:
            st.success("二创脚本生成成功！")

    # ── 展示已生成的脚本 ──────────────────────────
    if "creator_script_raw" in st.session_state:
        raw = st.session_state["creator_script_raw"]
        n   = st.session_state.get("creator_n_segments", 0)

        st.markdown("---")
        st.markdown("#### 生成的二创方案脚本")

        try:
            script = parse_script_json(raw)
            st.markdown(f"**创意方向：** {script.get('creative_direction', '—')}")
            st.markdown(f"**参考借鉴：** {script.get('style_reference', '—')}")

            segments = script.get("segments", [])
            st.markdown(f"#### 分段 SeedAnce 提示词（共 {len(segments)} 段）")

            seg_data = []
            for seg in segments:
                seg_data.append({
                    "分段":   f"第 {seg.get('segment_index','?')} 段 ({seg.get('time_range','—')})",
                    "原始内容摘要": seg.get("original_content_summary", ""),
                    "SeedAnce 提示词": seg.get("seedance_prompt", ""),
                })
            if seg_data:
                st.dataframe(
                    pd.DataFrame(seg_data),
                    use_container_width=True,
                    height=min(400, 60 + len(seg_data) * 55),
                )

            # 导出 JSON
            st.download_button(
                "📥 下载完整二创脚本 JSON",
                data=json.dumps(script, ensure_ascii=False, indent=2).encode("utf-8"),
                file_name="creator_recreation_script.json",
                mime="application/json",
            )

        except Exception as e:
            st.warning(f"JSON 解析失败（{e}），展示原始输出：")
            st.code(raw, language="text")

        st.markdown("---")
        st.markdown("#### Step 3 · SeedAnce 切片生成（9:16 竖版）")

        col_key1, col_key2 = st.columns(2)
        seedance_key = col_key1.text_input(
            "ARK API Key（留空使用系统内置 Key）",
            type="password",
            placeholder="留空则使用 creator_engine.py 内置 Key",
            help=(
                "ARK API Key 与普通 AK/SK 不同，格式为 ark-xxxx，"
                "在 console.volcengine.com/ark → API Key 管理 页面创建。"
                "如果留空，系统会使用 creator_engine.py 里配置的默认 Key。"
            ),
        )
        seedance_model = col_key2.selectbox(
            "SeedAnce 模型",
            ["doubao-seedance-2-0-260128", "doubao-seedance-2-0-fast-260128"],
            help="第一个为高质量版，第二个为快速版",
        )

        with st.expander("TOS 对象存储配置（视频分段上传用）", expanded=False):
            st.caption("SeedAnce API 需要通过 HTTP URL 传入视频，需先上传到 TOS 存储桶。留空则使用系统内置配置。")
            c_tos1, c_tos2 = st.columns(2)
            tos_endpoint   = c_tos1.text_input("TOS Endpoint", placeholder="留空用内置")
            tos_bucket     = c_tos2.text_input("TOS Bucket 名称", placeholder="留空用内置")
            c_tos3, c_tos4 = st.columns(2)
            tos_ak         = c_tos3.text_input("TOS Access Key", type="password", placeholder="留空用内置")
            tos_sk         = c_tos4.text_input("TOS Secret Key",  type="password", placeholder="留空用内置")

        can_run_seedance = bool(
            "creator_script_raw" in st.session_state
            and "creator_video_path" in st.session_state
        )

        if st.button("切片并启动 SeedAnce 生成", type="primary",
                     key="run_seedance", disabled=not can_run_seedance):

            video_path = st.session_state["creator_video_path"]
            script = parse_script_json(st.session_state["creator_script_raw"])

            # 只覆盖用户实际填写的字段，留空则沿用 creator_engine.py 里的默认值
            env_ov = {"SEEDANCE_MODEL": seedance_model}
            if seedance_key.strip(): env_ov["SEEDANCE_API_KEY"] = seedance_key.strip()
            if tos_endpoint.strip(): env_ov["TOS_ENDPOINT"]   = tos_endpoint.strip()
            if tos_bucket.strip():   env_ov["TOS_BUCKET"]     = tos_bucket.strip()
            if tos_ak.strip():       env_ov["TOS_ACCESS_KEY"] = tos_ak.strip()
            if tos_sk.strip():       env_ov["TOS_SECRET_KEY"] = tos_sk.strip()

            _run_seedance_streaming(video_path, script, env_ov)

    # ── Step 4: 已生成视频下载（始终可见）─────────────────────────────
    st.markdown("---")
    st.markdown("#### Step 4 · 下载已生成视频")
    _output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seedance_output")
    _out_files = sorted(
        [f for f in os.listdir(_output_dir) if f.endswith(".mp4")],
        key=lambda f: os.path.getmtime(os.path.join(_output_dir, f)),
        reverse=True,
    ) if os.path.isdir(_output_dir) else []

    if not _out_files:
        st.caption("暂无生成视频，运行 SeedAnce 后结果将出现在此处。")
    else:
        st.caption(f"共 {len(_out_files)} 个视频，按生成时间倒序排列")
        for _fname in _out_files:
            _fpath = os.path.join(_output_dir, _fname)
            _fsize = os.path.getsize(_fpath) / 1024 / 1024
            _c1, _c2 = st.columns([4, 1])
            _c1.caption(f"{_fname}  ({_fsize:.1f} MB)")
            with open(_fpath, "rb") as _fh:
                _c2.download_button(
                    "⬇ 下载",
                    data=_fh.read(),
                    file_name=_fname,
                    mime="video/mp4",
                    key=f"dl_{_fname}",
                )

