# -*- coding: utf-8 -*-
r"""
抖音视频/图文爬取下载工具
========================
基于 yt-dlp + Playwright 实现，自动处理抖音的反爬机制（X-Bogus 签名、msToken、验证码等）。

功能：
  - 支持单个/多个抖音视频链接下载
  - 支持抖音图文帖子（图片）下载，链接为 /note/ 或 /video/ 形式均可
    （图文走 Playwright 渲染页面提取轮播图片，API 被风控时自动降级）
  - 支持从文本文件批量读取链接
  - 支持视频合集（含图文帖子中的图片）
  - 自动保存到 D:\video 目录

用法示例：
  python dy "https://v.douyin.com/xxxxxxx/" --cookies-from-browser edge
  python dy "https://www.douyin.com/note/7xxxxxxxxxx" --cookies cookies.txt
  python dy "链接1" "链接2" --cookies cookies.txt
  python dy -f links.txt --cookies-from-browser chrome
  python dy -f links.txt -o D:\video\myfolder --cookies cookies.txt

重要说明（2024 年后抖音强制要求 cookies）：
  抖音接口现在需要有效 cookies 才能获取视频数据，否则报错：
    "Fresh cookies (not necessarily logged in) are needed"
  解决办法（任选其一）：
  1. 浏览器方式（最简单）：
       python dy "链接" --cookies-from-browser edge
       可选: edge / chrome / firefox / opera / vivaldi / brave
       注意: 使用 Chrome/Edge 前需先完全关闭浏览器进程
  2. cookies.txt 文件方式（最稳定）：
       a. 安装浏览器扩展 "Get cookies.txt LOCALLY"
       b. 打开 douyin.com 并登录，点击扩展导出 cookies.txt
       c. 运行: python dy "链接" --cookies cookies.txt

链接格式支持：
  - 分享短链接  https://v.douyin.com/xxxxxxx/
  - 完整链接    https://www.douyin.com/video/7xxxxxxxxxx
  - 用户主页    https://www.douyin.com/user/MS4wLjAB...
"""

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    print("[错误] 未安装 yt_dlp，请先执行: pip install yt-dlp")
    sys.exit(1)

# 默认保存目录
DEFAULT_SAVE_DIR = r"D:\070915\myDownload"

# 全局 User-Agent
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# 自动获取 cookies 所需的 Playwright
try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

# 抖音链接匹配正则
DOUYIN_URL_RE = re.compile(
    r"(https?://(?:v\.douyin\.com|www\.douyin\.com|iesdouyin\.com)/\S+)",
    re.IGNORECASE,
)


def sanitize_filename(name: str, max_len: int = 80) -> str:
    """清洗文件名中非法字符"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:max_len] if name else "unknown"


def extract_urls_from_text(text: str) -> list[str]:
    """从文本中提取所有抖音链接"""
    return list(dict.fromkeys(DOUYIN_URL_RE.findall(text)))


def read_urls_from_file(file_path: str) -> list[str]:
    """从文件读取链接（支持换行分隔或整段文本）"""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    urls = extract_urls_from_text(content)
    if not urls:
        # 兼容：每一行一个链接，去掉前后空白
        urls = [line.strip() for line in content.splitlines() if line.strip()]
    return list(dict.fromkeys(urls))


# 常见浏览器名称映射（cookies-from-browser 用）
BROWSER_CHOICES = ["edge", "chrome", "firefox", "opera", "vivaldi", "brave"]

# 常见的抖音 cookie 名（用于检查 cookies 是否有效）
DOUYIN_COOKIE_NAMES = ("s_v_web_id", "ttwid", "msToken", "odin_tt")


def build_ydl_opts(save_dir: str, cookies_file: str = "",
                   cookies_from_browser: str = "", list_only: bool = False) -> dict:
    """构建 yt-dlp 下载配置"""
    os.makedirs(save_dir, exist_ok=True)
    opts = {
        "skip_download": True if list_only else False,
        "quiet": True if list_only else False,
    }
    opts.update({
        "outtmpl": os.path.join(
            save_dir, "%(uploader)s - %(title).80s [%(id)s].%(ext)s"
        ),
        # 抖音视频多为无水印原片，使用 best 质量
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": False,          # 支持合集
        "ignoreerrors": True,         # 单条失败不中断批量
        "retries": 5,                 # 网络重试次数
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 30,
        "quiet": False,
        "no_warnings": False,
        "writethumbnail": True,
        # 禁用代理：系统可能残留失效的代理地址(如 127.0.0.1:7890)导致下载中断
        "proxy": "",
        # 断点续传 + 分片下载更稳定
        "continuedl": True,
        # 抖音需要的一些请求头
        "http_headers": {
            "User-Agent": USER_AGENT,
            "Referer": "https://www.douyin.com/",
        },
    })
    # 抖音接口需要 cookies，二选一注入
    if cookies_file:
        opts["cookiefile"] = cookies_file
    elif cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    return opts


def get_cookies_via_playwright(target_url: str = "") -> str | None:
    """用 Playwright 无头浏览器访问抖音自动获取 cookies，返回 cookies.txt 路径

    优先访问目标视频页（生成的签名 cookies 更有针对性），失败则回退到首页。
    最多尝试 3 次。
    """
    if not HAS_PLAYWRIGHT:
        print("[警告] 未安装 playwright，无法自动获取 cookies")
        print("       请执行: pip install playwright && playwright install chromium")
        return None

    for attempt in range(1, 4):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(
                    user_agent=USER_AGENT,
                    locale="zh-CN",
                    viewport={"width": 1920, "height": 1080},
                )
                page = ctx.new_page()
                # 优先访问目标视频页，让 JS 生成针对性签名 cookies
                first = target_url or "https://www.douyin.com/"
                print(f"[信息] 正在用无头浏览器打开抖音获取 cookies (尝试 {attempt}/3) ...")
                page.goto(first, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(8000)  # 等待 JS 生成签名 cookies
                # 模拟真人滚动页面，触发更多 JS 执行
                for _ in range(3):
                    page.mouse.wheel(0, 600)
                    page.wait_for_timeout(800)
                # 若目标页失败，回退首页再试
                if not ctx.cookies() and attempt == 1 and target_url:
                    page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(8000)
                cookies = ctx.cookies()
                browser.close()
        except Exception as e:
            print(f"[警告] Playwright 获取 cookies 失败: {e}")
            cookies = []

        if cookies:
            break
        print("[警告] 未获取到 cookies，重试 ...")

    if not cookies:
        print("[警告] Playwright 多次尝试均未获取到 cookies")
        return None

    # 导出 Netscape 格式到临时文件
    tmp = Path(tempfile.gettempdir()) / "douyin_cookies.txt"
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        if not c["domain"]:
            continue
        include_sub = "TRUE" if c["domain"].startswith(".") else "FALSE"
        # Playwright 中 expires=-1 表示会话 cookie，Netscape 格式需用 0
        expires = int(c.get("expires", 0) or 0)
        if expires < 0:
            expires = 0
        lines.append(
            f"{c['domain']}\t{include_sub}\t{c.get('path','/')}\t"
            f"{'TRUE' if c.get('secure') else 'FALSE'}\t{expires}\t"
            f"{c['name']}\t{c['value']}"
        )
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[完成] 已自动获取 {len(cookies)} 个 cookies: {tmp}")
    return str(tmp)


def resolve_short_url(url: str) -> str:
    """解析抖音短链接（v.douyin.com），跟随重定向返回最终完整 URL"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        resp = urllib.request.urlopen(req, timeout=20)
        return resp.geturl()
    except Exception as e:
        print(f"[警告] 短链接解析失败: {e}")
        return url


def build_cookie_header(cookies_file: str = "",
                        cookies_from_browser: str = "") -> str:
    """构建请求抖音 API 用的 Cookie 头（仅 douyin 域名），返回 'name=value; ...'"""
    if cookies_file:
        # 解析 Netscape 格式 cookies.txt
        try:
            with open(cookies_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.read().splitlines()
        except Exception as e:
            print(f"[警告] 读取 cookies 文件失败: {e}")
            return ""
        cookie_map: dict[str, str] = {}
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain = parts[0]
            if "douyin" not in domain and "amemv" not in domain:
                continue
            cookie_map[parts[5]] = parts[6]  # name=value，后者覆盖前者
        if cookie_map:
            return "; ".join(f"{k}={v}" for k, v in cookie_map.items())
        print("[警告] cookies.txt 中未找到 douyin 域名的 cookie")
        return ""
    if cookies_from_browser:
        try:
            from yt_dlp.cookies import extract_cookies_from_browser
            jar = extract_cookies_from_browser(cookies_from_browser)
        except Exception as e:
            print(f"[警告] 从浏览器读取 cookies 失败: {e}")
            return ""
        cookie_map = {
            c.name: c.value
            for c in jar
            if "douyin" in (c.domain or "") or "amemv" in (c.domain or "")
        }
        if cookie_map:
            return "; ".join(f"{k}={v}" for k, v in cookie_map.items())
        print(f"[警告] 浏览器 {cookies_from_browser} 中未找到 douyin 域名的 cookie")
        return ""
    return ""


def get_aweme_detail(aweme_id: str, cookie_header: str = "") -> dict:
    """请求抖音 aweme detail API，返回完整 JSON"""
    api_url = (
        "https://www.douyin.com/aweme/v1/web/aweme/detail/"
        f"?aweme_id={aweme_id}"
    )
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://www.douyin.com/",
        "Accept": "application/json, text/plain, */*",
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    req = urllib.request.Request(api_url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_image_urls(detail: dict) -> list[str]:
    """从 aweme detail JSON 中提取图文帖子的图片原图 URL 列表"""
    aweme = detail.get("aweme_detail") or {}
    images = aweme.get("images") or []
    urls = []
    for img in images:
        url = None
        # 优先取 url_list，其次 display_image / download_url_list
        url_list = img.get("url_list") or []
        if url_list:
            url = url_list[0]
        if not url:
            url_list = (img.get("display_image") or {}).get("url_list") or []
            if url_list:
                url = url_list[0]
        if not url:
            url_list = img.get("download_url_list") or []
            if url_list:
                url = url_list[0]
        if url:
            urls.append(url)
    return list(dict.fromkeys(urls))


def download_images(url: str, save_dir: str, cookies_file: str = "",
                    cookies_from_browser: str = "") -> bool:
    """下载抖音图文帖子的所有图片（yt-dlp 不支持，用 Playwright 渲染页面提取）

    链接格式: https://www.douyin.com/note/7xxxxxxxxxx
    或       https://www.douyin.com/video/7xxxxxxxxxx（部分图文的视频链接）
    或       https://v.douyin.com/xxxxxxx/（短链接，自动解析）
    """
    if "v.douyin.com" in url:
        url = resolve_short_url(url)
        print(f"[信息] 解析短链接 -> {url}")
    m = re.search(r"(?:video|note)/(\d+)", url)
    if not m:
        print(f"[失败] 无法从链接提取帖子 ID: {url}")
        return False
    aweme_id = m.group(1)
    print(f"\n{'=' * 60}")
    print(f"[图文] 帖子 ID: {aweme_id}")

    # 先尝试直接调 API（需要新鲜 cookies，可能被风控返回空）
    cookie_header = build_cookie_header(cookies_file, cookies_from_browser)
    images = []
    title = ""
    author = ""
    if cookie_header:
        try:
            detail = get_aweme_detail(aweme_id, cookie_header)
            images = extract_image_urls(detail)
            aweme = detail.get("aweme_detail") or {}
            title = (aweme.get("desc") or "").strip()
            author = ((aweme.get("author") or {}).get("nickname") or "").strip()
        except Exception:
            images = []
    if images:
        print(f"[信息] API 获取成功: {len(images)} 张图片")
    else:
        # API 被风控/无 cookies → 用 Playwright 渲染页面提取轮播图
        if not HAS_PLAYWRIGHT:
            print("[失败] 图文下载需要 Playwright（自动获取页面数据）")
            print("       请执行: pip install playwright && playwright install chromium")
            print("       并确认用虚拟环境运行: d:\\code\\.venv\\Scripts\\python.exe dy.py")
            return False
        print("[信息] API 被风控，改用 Playwright 渲染页面提取图片 ...")
        ok, images, title, author = extract_note_images_playwright(aweme_id)
        if not ok:
            return False

    os.makedirs(save_dir, exist_ok=True)
    title_clean = sanitize_filename(title or f"douyin_note_{aweme_id}")
    author_clean = sanitize_filename(author)
    print(f"[信息] 作者: {author_clean or '未知'} | 标题: {title_clean} | 共 {len(images)} 张图片")

    # 逐张下载（签名 URL 需原样下载，不能用 yt-dlp 因为会改 URL）
    ok_count = 0
    for i, img_url in enumerate(images, 1):
        prefix = f"{author_clean} - " if author_clean else ""
        # 根据 URL 推断扩展名
        url_base = img_url.split("?")[0]
        if ".webp" in url_base or "webp" in url_base:
            ext = "webp"
        elif ".png" in url_base:
            ext = "png"
        elif ".gif" in url_base:
            ext = "gif"
        else:
            ext = "jpg"
        filename = os.path.join(
            save_dir, f"{prefix}{title_clean} - {i:02d}.{ext}")
        try:
            req = urllib.request.Request(img_url, headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://www.douyin.com/",
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            with open(filename, "wb") as f:
                f.write(data)
            print(f"  [完成] {filename} ({len(data)//1024} KB)")
            ok_count += 1
        except Exception as e:
            print(f"  [失败] 第 {i} 张: {e}")
    print(f"[完成] 图文下载完成 {ok_count}/{len(images)}，保存到: {save_dir}")
    return ok_count > 0


def extract_note_images_playwright(aweme_id: str) -> tuple[bool, list[str], str, str]:
    """用 Playwright 渲染 note 页面，提取轮播区图片 URL + 标题 + 作者

    返回: (是否成功, 图片URL列表, 标题, 作者)
    """
    if not HAS_PLAYWRIGHT:
        return False, [], "", ""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=USER_AGENT, locale="zh-CN",
                viewport={"width": 1920, "height": 1080},
            )
            page = ctx.new_page()
            print("[信息] 正在打开抖音图文页面 ...")
            page.goto(
                f"https://www.douyin.com/note/{aweme_id}",
                wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)  # 等待图片渲染
            # 滚动触发懒加载
            for _ in range(6):
                page.mouse.wheel(0, 400)
                page.wait_for_timeout(500)
            # 提取轮播区图片（locator API）
            slides = page.locator('.dySwiperSlide img, [class*="SwiperSlide"] img')
            urls = []
            n = slides.count()
            for i in range(n):
                src = slides.nth(i).get_attribute("src") or ""
                if src.startswith("http") and src not in urls:
                    urls.append(src)
            title = page.title().replace(" - 抖音", "").strip()
            # 提取作者昵称（用户信息区）
            author = ""
            for sel in (
                '[data-e2e="note-detail"] a[href*="/user/"]',
                '[data-e2e="note-detail"] [class*="userName"]',
                '[data-e2e="note-user-name"]',
                'a[href*="/user/MS4w"]',
            ):
                try:
                    el = page.query_selector(sel)
                    if el:
                        author = (el.inner_text() or "").strip()
                        if author:
                            break
                except Exception:
                    continue
            browser.close()
        if not urls:
            print("[失败] 未在页面中找到轮播图片")
            return False, [], "", ""
        return True, urls, title, author
    except Exception as e:
        print(f"[失败] Playwright 提取图片出错: {e}")
        return False, [], "", ""


def download_single(url: str, save_dir: str, cookies_file: str = "",
                    cookies_from_browser: str = "") -> bool:
    """下载单个链接，返回是否成功"""
    print(f"\n{'=' * 60}")
    print(f"[开始] 链接: {url}")
    try:
        with yt_dlp.YoutubeDL(
            build_ydl_opts(save_dir, cookies_file, cookies_from_browser)
        ) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                print(f"[失败] 无法解析链接: {url}")
                return False
            entries = info.get("entries") or [info]
            print(f"[完成] 共下载 {len(entries)} 个条目")
            return True
    except yt_dlp.utils.DownloadError as e:
        print(f"[失败] 下载出错: {e}")
    except Exception as e:
        print(f"[失败] 未知错误: {e}")
    return False


def list_formats(url: str, cookies_file: str = "",
                 cookies_from_browser: str = "") -> bool:
    """只列出链接的所有可用格式（不下载），用于排查画质问题"""
    print(f"\n[格式] 链接: {url}")
    try:
        with yt_dlp.YoutubeDL(
            build_ydl_opts("", cookies_file, cookies_from_browser, list_only=True)
        ) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                print("[失败] 无法解析链接")
                return False
            entries = info.get("entries") or [info]
            for e in entries:
                fmts = e.get("formats") or ([e] if e.get("url") else [])
                print(f"\n条目: {e.get('title', '?')}  ({e.get('id', '?')})")
                if not fmts:
                    print("  (无格式信息)")
                for f in fmts:
                    h = f.get("height")
                    w = f.get("width")
                    print(
                        f"  {str(f.get('format_id', '?')):<12} "
                        f"{f'{h}x{w}' if h and w else '?'}  "
                        f"{str(f.get('fps', '?'))}fps  "
                        f"{str(f.get('vcodec', '?')):<8} "
                        f"{str(f.get('format_note') or f.get('quality') or '')}"
                    )
            return True
    except yt_dlp.utils.DownloadError as e:
        print(f"[失败] 出错: {e}")
    except Exception as e:
        print(f"[失败] 未知错误: {e}")
    return False


def load_cookies_file(cookies_file: str) -> None:
    """校验 cookies.txt 是否包含抖音的有效 cookie，并给出提示"""
    if not Path(cookies_file).exists():
        print(f"[错误] cookies 文件不存在: {cookies_file}")
        return
    try:
        with open(cookies_file, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        print(f"[错误] 读取 cookies 文件失败: {e}")
        return
    found = [c for c in DOUYIN_COOKIE_NAMES if c in content]
    if found:
        print(f"[信息] cookies 文件中检测到抖音相关 cookie: {', '.join(found)}")
    else:
        print("[警告] cookies 文件中未检测到抖音相关 cookie！")
        print("       请确保导出的 cookies 包含 douyin.com 域名的条目。")


def main():
    parser = argparse.ArgumentParser(
        description="抖音视频爬取下载工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("urls", nargs="*", help="抖音视频链接（可多个）")
    parser.add_argument(
        "-f", "--file", help="包含链接的文本文件路径（可批量）"
    )
    parser.add_argument(
        "-o", "--output",
        default=DEFAULT_SAVE_DIR,
        help=f"保存目录（默认: {DEFAULT_SAVE_DIR}）",
    )
    parser.add_argument(
        "-F", "--list-formats", action="store_true",
        help="只列出每个链接的可用格式（不下载），用于排查画质",
    )
    # cookies 相关参数（抖音反爬需要）
    cookies_group = parser.add_mutually_exclusive_group()
    cookies_group.add_argument(
        "--cookies",
        metavar="FILE",
        help="Netscape 格式的 cookies.txt 文件路径（推荐方式）",
    )
    cookies_group.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER",
        choices=BROWSER_CHOICES,
        help="从浏览器读取 cookies（可选: %(choices)s）",
    )
    args = parser.parse_args()

    # 收集所有链接
    url_list: list[str] = []
    for u in args.urls:
        url_list.extend(extract_urls_from_text(u) or [u])
    if args.file:
        if not Path(args.file).exists():
            print(f"[错误] 文件不存在: {args.file}")
            sys.exit(1)
        url_list.extend(read_urls_from_file(args.file))

    # 去重
    url_list = list(dict.fromkeys(url_list))

    if not url_list:
        parser.print_help()
        print("\n[提示] 请提供至少一个抖音链接，例如:")
        print('  python dy "https://v.douyin.com/xxxxxxx/"')
        print('  python dy -f links.txt')
        sys.exit(1)

    save_dir = args.output
    print(f"[信息] 共 {len(url_list)} 个链接，保存到: {save_dir}")

    # cookies 逻辑
    cookies_file = args.cookies or ""
    auto_cookies = False  # 是否使用自动获取的 cookies（用于失败重试）
    if args.cookies:
        load_cookies_file(args.cookies)
        print(f"[信息] 使用 cookies 文件: {args.cookies}")
    elif args.cookies_from_browser:
        print(f"[信息] 使用浏览器 cookies: {args.cookies_from_browser}")
        print("       [注意] 若失败，请先完全关闭该浏览器再重试")
    else:
        # 未提供 cookies → 自动用 Playwright 获取
        print("[信息] 未提供 cookies，尝试自动获取 ...")
        auto = get_cookies_via_playwright(url_list[0])
        if auto:
            cookies_file = auto
            auto_cookies = True
        else:
            print("[警告] 自动获取失败！抖音接口需要 cookies，否则会报错:")
            print('       "Fresh cookies (not necessarily logged in) are needed"')
            print("       解决方式: 加参数 --cookies-from-browser edge/chrome/firefox")
            print("       或加参数 --cookies cookies.txt")

    print(f"[信息] 正在初始化 yt-dlp ...")

    success = 0
    for i, url in enumerate(url_list, 1):
        print(f"\n[进度] ({i}/{len(url_list)})")
        if args.list_formats:
            # 只列格式不下载
            ok = list_formats(url, cookies_file,
                              args.cookies_from_browser or "")
            if ok:
                success += 1
            continue
        # 短链接先解析，判断是视频还是图文
        real_url = resolve_short_url(url) if "v.douyin.com" in url else url
        if "/note/" in real_url:
            # 图文帖子：yt-dlp 不识别 /note/ 路径，直接走图文下载
            ok = download_images(real_url, save_dir, cookies_file,
                                 args.cookies_from_browser or "")
        else:
            ok = download_single(url, save_dir, cookies_file,
                                 args.cookies_from_browser or "")
            if not ok:
                # 部分图文帖子的分享链接是 /video/ 路径，yt-dlp 无法解析
                # images 字段（会报"找不到视频格式"），用图文下载兜底
                ok = download_images(real_url, save_dir, cookies_file,
                                     args.cookies_from_browser or "")
        # 自动 cookies 失败时：重新获取 cookies 重试一次（cookies 可能失效/不完整）
        if not ok and auto_cookies:
            print("\n[信息] 下载失败，重新获取 cookies 后重试 ...")
            new_cookies = get_cookies_via_playwright(url)
            if new_cookies:
                cookies_file = new_cookies
                if "/note/" in real_url:
                    ok = download_images(real_url, save_dir, cookies_file,
                                         args.cookies_from_browser or "")
                else:
                    ok = download_single(url, save_dir, cookies_file,
                                         args.cookies_from_browser or "")
                    if not ok:
                        ok = download_images(real_url, save_dir, cookies_file,
                                             args.cookies_from_browser or "")
        if ok:
            success += 1

    print(f"\n{'=' * 60}")
    print(f"[总结] 成功 {success}/{len(url_list)}，视频已保存到: {save_dir}")


if __name__ == "__main__":
    main()
