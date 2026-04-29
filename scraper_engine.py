import os
import re
import time
import random
import yt_dlp
from urllib.parse import quote

VIDEOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "videos")
CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")

os.makedirs(VIDEOS_DIR, exist_ok=True)

# 抖音必须用桌面 UA：短链 v.douyin.com 遇到手机 UA 会跳转到 iesdouyin.com，yt-dlp 不支持
_DOUYIN_UAS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]
_TIKTOK_UAS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36",
]


def _get_cookie_file(platform):
    """返回对应平台的 Cookie 文件路径（文件存在且有实质内容则返回）"""
    path = os.path.join(CONFIG_DIR, f"cookies_{platform.lower()}.txt")
    if os.path.exists(path) and os.path.getsize(path) > 200:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            real_lines = [l for l in f if l.strip() and not l.startswith("#")]
        if real_lines:
            return path
    return None


def _get_proxy():
    return (os.environ.get("HTTP_PROXY") or
            os.environ.get("http_proxy") or
            os.environ.get("HTTPS_PROXY") or
            os.environ.get("https_proxy"))


def _base_ydl_opts(platform):
    """构建基础 yt-dlp 配置：UA 伪装 + Cookie + 代理 + 请求节奏"""
    ua = random.choice(_DOUYIN_UAS if platform == "Douyin" else _TIKTOK_UAS)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "http_headers": {
            "User-Agent": ua,
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Referer": "https://www.douyin.com/" if platform == "Douyin" else "https://www.tiktok.com/",
        },
        "sleep_interval": 1,
        "max_sleep_interval": 3,
        "retries": 5,
        "fragment_retries": 5,
    }
    cookie_file = _get_cookie_file(platform)
    if cookie_file:
        opts["cookiefile"] = cookie_file
    proxy = _get_proxy()
    if proxy:
        opts["proxy"] = proxy
    return opts, cookie_file, proxy


# ─────────────────────────────────────────────────────────
# Playwright-based search helpers
# ─────────────────────────────────────────────────────────

def _load_cookies_for_playwright(cookie_file):
    """将 Netscape Cookie 文件转换为 Playwright 可用的 cookie 列表。"""
    cookies = []
    if not cookie_file or not os.path.exists(cookie_file):
        return cookies
    with open(cookie_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _, path, secure, expiry, name, value = parts[:7]
            cookie = {
                "name":   name,
                "value":  value,
                "domain": domain,
                "path":   path,
                "secure": secure.upper() == "TRUE",
            }
            try:
                exp = int(expiry)
                if exp > 0:
                    cookie["expires"] = exp
            except (ValueError, TypeError):
                pass
            cookies.append(cookie)
    return cookies


def _search_douyin_playwright(keyword, top_n, cookie_file, proxy):
    """用 Playwright 打开抖音搜索页，提取视频链接。"""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    search_url = f"https://www.douyin.com/search/{quote(keyword)}?type=video"
    ua = random.choice(_DOUYIN_UAS)
    cookies = _load_cookies_for_playwright(cookie_file)

    results = []
    print(f"    [Playwright] 打开 {search_url}")

    proxy_settings = None
    if proxy:
        proxy_settings = {"server": proxy}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=ua,
            locale="zh-CN",
            viewport={"width": 1280, "height": 800},
            proxy=proxy_settings,
        )
        if cookies:
            ctx.add_cookies(cookies)

        page = ctx.new_page()
        try:
            page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(2)

            # 关闭可能出现的登录弹窗
            try:
                page.keyboard.press("Escape")
                time.sleep(0.5)
            except Exception:
                pass

            # 等待视频卡片出现（尝试多种选择器）
            for sel in ['a[href*="/video/"]', '[data-e2e="search-video-card"]', '[class*="video-card"]']:
                try:
                    page.wait_for_selector(sel, timeout=8000)
                    break
                except PWTimeout:
                    pass

            # 滚动触发懒加载，最多 5 次
            for _ in range(5):
                page.mouse.wheel(0, 600)
                time.sleep(1.5)
                # 从 HTML 中直接提取视频 ID（比 DOM selector 更可靠）
                html = page.content()
                ids = set(re.findall(r'/video/(\d{15,20})', html))
                if len(ids) >= top_n:
                    break

            # 从完整 HTML 提取所有视频 ID
            html = page.content()
            ids = list(dict.fromkeys(re.findall(r'/video/(\d{15,20})', html)))

            for vid in ids[:top_n]:
                # 尝试找对应标题
                title = f"{keyword}_{vid}"
                results.append({
                    "title":      title,
                    "url":        f"https://www.douyin.com/video/{vid}",
                    "view_count": 0,
                    "like_count": 0,
                    "duration":   0,
                })

        except Exception as e:
            print(f"    [Playwright 异常] {e}")
        finally:
            browser.close()

    return results


def _search_tiktok_playwright(keyword, top_n, cookie_file, proxy):
    """用 Playwright 打开 TikTok 搜索页，提取视频链接。"""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    search_url = f"https://www.tiktok.com/search?q={quote(keyword)}"
    ua = random.choice(_TIKTOK_UAS)
    cookies = _load_cookies_for_playwright(cookie_file)

    results = []
    print(f"    [Playwright] 打开 {search_url}")

    proxy_settings = None
    if proxy:
        proxy_settings = {"server": proxy}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=ua,
            locale="en-US",
            viewport={"width": 390, "height": 844},
            proxy=proxy_settings,
        )
        if cookies:
            ctx.add_cookies(cookies)

        page = ctx.new_page()
        try:
            page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
            try:
                page.wait_for_selector('a[href*="/video/"]', timeout=15000)
            except PWTimeout:
                print("    [警告] 等待超时，尝试直接提取已加载内容")

            for _ in range(3):
                page.mouse.wheel(0, 800)
                time.sleep(1.5)
                links = page.query_selector_all('a[href*="/video/"]')
                ids = set()
                for a in links:
                    href = a.get_attribute("href") or ""
                    m = re.search(r'/@[^/]+/video/(\d+)', href)
                    if m:
                        ids.add(m.group(1))
                if len(ids) >= top_n:
                    break

            links = page.query_selector_all('a[href*="/video/"]')
            seen = set()
            for a in links:
                href = a.get_attribute("href") or ""
                m = re.search(r'/@([^/]+)/video/(\d+)', href)
                if not m:
                    continue
                user, vid = m.group(1), m.group(2)
                if vid in seen:
                    continue
                seen.add(vid)
                title_el = a.query_selector('[class*="desc"], p, span')
                title = (title_el.inner_text().strip() if title_el else "") or f"{keyword}_{vid}"
                results.append({
                    "title":      title[:80],
                    "url":        f"https://www.tiktok.com/@{user}/video/{vid}",
                    "view_count": 0,
                    "like_count": 0,
                    "duration":   0,
                })
                if len(results) >= top_n:
                    break

        except Exception as e:
            print(f"    [Playwright 异常] {e}")
        finally:
            browser.close()

    return results


def scrape_top_videos(platform, keyword, top_n=5):
    """
    使用 Playwright 爬取搜索页视频链接，返回元数据列表。

    【依赖条件】
    - pip install playwright && playwright install chromium
    - 配置有效 Cookie（config/cookies_douyin.txt / cookies_tiktok.txt）
    - TikTok 建议配置海外代理（HTTP_PROXY 环境变量）

    返回: [{"title": str, "url": str, "view_count": int, "like_count": int, "duration": float}]
    """
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("[错误] playwright 未安装，请运行：pip install playwright && playwright install chromium")
        return []

    cookie_file = _get_cookie_file(platform)
    proxy = _get_proxy()

    if cookie_file:
        print(f"    [Cookie ✓] {os.path.basename(cookie_file)}")
    else:
        print(f"    [警告] 未找到有效 Cookie，搜索大概率失败。")
        print(f"           请在 Web UI【🍪 Cookie配置】页 或直接编辑 config/cookies_{platform.lower()}.txt")
    if proxy:
        print(f"    [代理] {proxy}")

    print(f"[*] {platform} Playwright 搜索「{keyword}」(top {top_n})")

    if platform == "Douyin":
        results = _search_douyin_playwright(keyword, top_n, cookie_file, proxy)
    else:
        results = _search_tiktok_playwright(keyword, top_n, cookie_file, proxy)

    print(f"    [+] 获取到 {len(results)} 条结果")
    return results


def download_video(url, title, keyword, platform, max_retries=3):
    """
    使用 yt-dlp 下载短视频。
    内置：Cookie 注入、UA 伪装、代理、指数退避重试（最多 max_retries 次）。
    成功返回本地文件路径，失败返回 None。
    """
    safe_title = "".join(c for c in title if c.isalpha() or c.isdigit() or c == " ").rstrip()
    if not safe_title:
        safe_title = "video"

    output_template = os.path.join(
        VIDEOS_DIR, f"{platform}_{keyword}_{safe_title}_%(id)s.%(ext)s"
    )

    ydl_opts, _, _ = _base_ydl_opts(platform)
    ydl_opts.update({
        "outtmpl": output_template,
        "format":  "mp4/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    })

    for attempt in range(1, max_retries + 1):
        try:
            print(f"    [下载] 尝试 {attempt}/{max_retries}: {url}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                if os.path.exists(filename):
                    return filename
                for ext in ["mp4", "webm", "mkv", "mov"]:
                    alt = os.path.splitext(filename)[0] + f".{ext}"
                    if os.path.exists(alt):
                        return alt
        except Exception as e:
            print(f"    [-] 下载失败 (尝试 {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                wait = 2 ** attempt
                print(f"    [重试] {wait}s 后重试...")
                time.sleep(wait)

    print(f"    [-] 超出最大重试次数，放弃: {url}")
    return None
