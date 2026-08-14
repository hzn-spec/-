# -*- coding: utf-8 -*-
r"""
抖音账号喜欢列表视频链接提取工具
================================
用 Playwright 打开指定抖音账号的"喜欢"列表页，滚动加载全部内容，
提取所有视频（含图文笔记）链接，整理到一个 txt 文件。
输出的文件可直接交给主程序批量下载：

  python dy.py -f like_links.txt

用法：

  python URL_get.py "https://www.douyin.com/user/MS4wLjABAAAAxxxx"
  python URL_get.py "https://v.douyin.com/xxxxxxx/" -o like_links.txt
  python URL_get.py "https://www.douyin.com/user/xxx" --cookies-from-browser edge
  python URL_get.py "https://www.douyin.com/user/xxx" --cookies cookies.txt

参数：
  -o, --output              输出文件（默认 like_links.txt）
  --cookies FILE            抖音 cookies.txt（Netscape 格式，看私密喜欢列表时需要）
  --cookies-from-browser B  从浏览器读取登录 cookies（edge/chrome/firefox）
  --tab TYPE                列表类型：like（喜欢，默认）/ post（作品）
  --sub-tab TYPE            子标签：video（仅视频）/ image（仅图文）/ all（全部，默认）
  --max-scroll N            最大滚动次数（默认 100，用于加载更多内容）

注意：
  * 多数账号的喜欢列表是"仅自己可见"，游客无法访问；
    此时请用 --cookies-from-browser 或 --cookies 传入登录态的 cookies。
  * 抖音主页正确参数是 ?showTab=like（不要用 ?tab=like，前端不识别会导致
    抓到默认视图的推荐内容，与页面显示不符）。
  * 需要 playwright + chromium（与主程序同一环境）。
"""
import argparse
import re
import sys
import urllib.request
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# 视频/图文链接正则（相对路径）
SEC_UID_RE = re.compile(r"douyin\.com/user/([A-Za-z0-9_\-]+)")
SHORT_LINK_RE = re.compile(r"https?://v\.douyin\.com/\S+")


def resolve_short_url(url: str) -> str:
    """跟随 v.douyin.com 短链重定向，返回真实 URL"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.geturl()
    except Exception as e:
        print(f"[警告] 短链接解析失败: {e}")
        return url


def get_sec_uid(url: str) -> str:
    """从账号主页链接提取 sec_uid"""
    m = SEC_UID_RE.search(url)
    if m:
        return m.group(1)
    raise ValueError(
        f"无法从链接中提取账号 ID: {url}\n"
        "请提供账号主页链接，例如: https://www.douyin.com/user/MS4wLjABAAAAxxxx"
    )


def parse_netscape_cookies(file_path: str) -> list[dict]:
    """解析 Netscape 格式 cookies.txt 为 Playwright add_cookies 所需格式"""
    cookies = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()
    except Exception as e:
        print(f"[错误] 读取 cookies 文件失败: {e}")
        sys.exit(1)
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _, path, _, expires, name, value = parts[:7]
        if "douyin" not in domain and "amemv" not in domain:
            continue
        cookie = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path or "/",
        }
        try:
            exp = int(expires)
            if exp > 0:
                cookie["expires"] = exp
        except (ValueError, TypeError):
            pass
        cookies.append(cookie)
    return cookies


def load_browser_cookies(browser: str) -> list[dict]:
    """从浏览器读取 douyin cookies（复用 yt-dlp 的解析）"""
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
        jar = extract_cookies_from_browser(browser)
    except Exception as e:
        print(f"[错误] 从浏览器读取 cookies 失败: {e}")
        sys.exit(1)
    cookies = []
    for c in jar:
        if "douyin" not in (c.domain or "") and "amemv" not in (c.domain or ""):
            continue
        cookie = {
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path or "/",
        }
        if c.expires and c.expires > 0:
            cookie["expires"] = int(c.expires)
        cookies.append(cookie)
    return cookies


# 抖音用户页列表容器（喜欢/作品），只抓容器内链接，避免混入右侧推荐栏内容
LIST_CONTAINERS = (
    '[data-e2e="user-like-list"]',
    '[data-e2e="user-post-list"]',
    '[data-e2e="user-work-list"]',
    '[data-e2e="scroll-list"]',
)

# 登录弹窗全屏遮罩（类名会变，用 id 和 login-panel 特征匹配）
LOGIN_OVERLAY_JS = """() => {
  const sel = [
    '#login-panel-new',
    '.fe8GGOyG',
    '[class*="login-panel"]',
    '[class*="login_panel"]',
  ];
  const el = sel.map(s => document.querySelector(s)).find(Boolean);
  if (!el) return false;
  // 先尝试点关闭按钮
  const btn = el.querySelector('[class*="Close"], [class*="close"], [aria-label*="关闭"], svg');
  if (btn) btn.click();
  // 再直接移除遮罩并恢复页面滚动
  el.remove();
  document.body.style.overflow = '';
  document.documentElement.style.overflow = '';
  return true;
}"""


def close_login_overlay(page) -> None:
    """移除抖音全屏登录弹窗遮罩（否则遮挡页面，滚动/点击全部失效）"""
    try:
        removed = page.evaluate(LOGIN_OVERLAY_JS)
        if removed:
            print("[信息] 已关闭登录弹窗遮罩")
            page.wait_for_timeout(1000)
    except Exception:
        pass


def scroll_page(page) -> None:
    """滚动页面触发列表懒加载。

    新版抖音主页 body 是 overflow:hidden，真正可滚动的是包含列表的
    内容容器（如 .parent-route-container），所以从列表容器向上找
    scrollHeight > clientHeight 的祖先来滚动；找不到时退回鼠标 wheel。
    """
    ok = page.evaluate("""() => {
      const list = document.querySelector('[data-e2e="user-post-list"], '
        + '[data-e2e="user-like-list"]');
      let cur = list;
      for (let i = 0; i < 8 && cur; i++) {
        if (cur.scrollHeight > cur.clientHeight + 5) {
          cur.scrollTop += 800;
          return true;
        }
        cur = cur.parentElement;
      }
      const alt = document.querySelector('.parent-route-container, .route-sc');
      if (alt) {
        alt.scrollTop += 800;
        return true;
      }
      return false;
    }""")
    if not ok:
        # 兜底：鼠标移到列表上方再 wheel（老版页面结构）
        page.mouse.move(960, 600)
        page.mouse.wheel(0, 800)


def extract_links_from_page(page) -> tuple[list[str], list[str]]:
    """从当前页面 DOM 提取视频 /video/ 与图文 /note/ 链接（保持文档顺序去重）。

    优先只抓列表容器（user-like-list / user-post-list / scroll-list）内的链接，
    避免把页面右侧"相关推荐"的内容误当成列表内容。
    """
    videos: list[str] = []
    notes: list[str] = []
    seen_v: set[str] = set()
    seen_n: set[str] = set()

    # 找出存在的列表容器
    containers = page.locator(", ".join(LIST_CONTAINERS))
    hrefs: list[str] = []
    if containers.count() > 0:
        hrefs = containers.evaluate_all(
            "els => els.flatMap(e => "
            "  [...e.querySelectorAll('a[href*=\"/video/\"], a[href*=\"/note/\"]')]"
            "    .map(a => a.getAttribute('href') || ''))"
        )
    else:
        # 兼容：找不到容器时退回全页面抓取
        hrefs = page.locator('a[href*="/video/"], a[href*="/note/"]').evaluate_all(
            "els => els.map(e => e.getAttribute('href') || '')")

    for link in hrefs:
        m = re.search(r"/(video|note)/(\d+)", link)
        if not m:
            continue
        kind, vid = m.group(1), m.group(2)
        url = f"https://www.douyin.com/{kind}/{vid}"
        if kind == "video":
            if vid not in seen_v:
                seen_v.add(vid)
                videos.append(url)
        else:
            if vid not in seen_n:
                seen_n.add(vid)
                notes.append(url)
    return videos, notes


def collect_links(sec_uid: str, tab: str, sub_tab: str, max_scroll: int,
                  extra_cookies: list[dict]) -> tuple[list[str], list[str]]:
    """滚动加载喜欢/作品列表，返回 (视频链接, 图文链接)"""
    from playwright.sync_api import sync_playwright

    all_videos: list[str] = []
    all_notes: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=USER_AGENT,
            locale="zh-CN",
            viewport={"width": 1920, "height": 1080},
        )
        if extra_cookies:
            ctx.add_cookies(extra_cookies)
        page = ctx.new_page()
        # 抖音主页正确的参数是 ?showTab=xxx（?tab=xxx 前端不识别）
        tab_url = f"https://www.douyin.com/user/{sec_uid}?showTab={tab}"
        if sub_tab and sub_tab != "all":
            tab_url += f"&showSubTab={sub_tab}"
        print(f"[信息] 正在打开: {tab_url}")
        page.goto(tab_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)  # 等待首屏渲染

        # 若 ?showTab= 未生效，尝试点击对应 tab
        tab_keyword = "喜欢" if tab == "like" else "作品"
        try:
            tab_btn = page.locator(
                f'[data-e2e="user-tab-{tab}"], '
                f'a:has-text("{tab_keyword}"), [role="tab"]:has-text("{tab_keyword}")'
            ).first
            if tab_btn.count():
                tab_btn.click(timeout=3000)
                page.wait_for_timeout(3000)
        except Exception:
            pass

        # 关闭/移除全屏登录弹窗遮罩（否则挡住页面导致无法滚动）
        close_login_overlay(page)

        v, n = extract_links_from_page(page)
        all_videos = list(dict.fromkeys(all_videos + v))
        all_notes = list(dict.fromkeys(all_notes + n))
        print(f"[信息] 首屏: 视频 {len(all_videos)} 个, 图文 {len(all_notes)} 个")

        # 滚动加载，直到连续多次无新增
        stale = 0
        for i in range(max_scroll):
            before = len(all_videos) + len(all_notes)
            scroll_page(page)
            page.wait_for_timeout(900)
            v, n = extract_links_from_page(page)
            all_videos = list(dict.fromkeys(all_videos + v))
            all_notes = list(dict.fromkeys(all_notes + n))
            after = len(all_videos) + len(all_notes)
            if after > before:
                stale = 0
                print(f"[信息] 滚动 {i + 1}: 累计 视频 {len(all_videos)} 图文 {len(all_notes)}")
            else:
                stale += 1
                if stale >= 4:
                    print(f"[信息] 已加载到底（连续 {stale} 次无新增）")
                    break
        browser.close()

    # 按子标签过滤（showSubTab 只影响渲染，DOM 里可能混入其他内容）
    if sub_tab == "video":
        all_notes = []
    elif sub_tab == "image":
        all_videos = []
    return all_videos, all_notes


def main():
    parser = argparse.ArgumentParser(
        description="提取抖音账号喜欢/作品列表的全部视频链接到文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="抖音账号主页链接或 v.douyin.com 分享短链")
    parser.add_argument("-o", "--output", default="like_links.txt",
                        help="输出文件（默认 like_links.txt）")
    parser.add_argument("--cookies", metavar="FILE",
                        help="抖音 cookies.txt（Netscape 格式）")
    parser.add_argument("--cookies-from-browser", metavar="BROWSER",
                        choices=["edge", "chrome", "firefox", "opera", "vivaldi", "brave"],
                        help="从浏览器读取登录 cookies")
    parser.add_argument("--tab", choices=["like", "post"], default="like",
                        help="列表类型: like=喜欢(默认) / post=作品")
    parser.add_argument("--sub-tab", choices=["video", "image", "all"],
                        default="all",
                        help="子标签: video=仅视频 / image=仅图文 / all=全部(默认)")
    parser.add_argument("--max-scroll", type=int, default=100,
                        help="最大滚动次数（默认 100）")
    args = parser.parse_args()

    # 解析账号链接（支持短链）
    url = args.url.strip()
    if SHORT_LINK_RE.match(url):
        print(f"[信息] 正在解析短链: {url}")
        url = resolve_short_url(url)
        print(f"[信息] 解析到: {url}")
    try:
        sec_uid = get_sec_uid(url)
    except ValueError as e:
        print(f"[错误] {e}")
        sys.exit(1)

    # 加载 cookies
    extra_cookies = []
    if args.cookies:
        extra_cookies = parse_netscape_cookies(args.cookies)
        print(f"[信息] 已加载 cookies 文件: {args.cookies} ({len(extra_cookies)} 条)")
    elif args.cookies_from_browser:
        extra_cookies = load_browser_cookies(args.cookies_from_browser)
        print(f"[信息] 已从 {args.cookies_from_browser} 读取 cookies ({len(extra_cookies)} 条)")

    # 开始收集
    tab_name = "喜欢" if args.tab == "like" else "作品"
    sub_name = {"video": "仅视频", "image": "仅图文", "all": "全部"}[args.sub_tab]
    print(f"[信息] 账号 {sec_uid}，列表: {tab_name}（{sub_name}），最大滚动: {args.max_scroll}")
    videos, notes = collect_links(sec_uid, args.tab, args.sub_tab,
                                  args.max_scroll, extra_cookies)

    all_links = videos + notes
    if not all_links:
        print("\n[失败] 未提取到任何链接。可能原因:")
        print("  1. 该账号的喜欢列表是'仅自己可见'（游客看不到）→ 加 --cookies-from-browser edge 登录后再试")
        print("  2. 账号不存在或已被封禁")
        sys.exit(1)

    # 写入文件（每行一个链接，主程序 -f 可直接识别）
    out = Path(args.output)
    out.write_text("\n".join(all_links) + "\n", encoding="utf-8")
    print(f"\n[完成] 共 {len(videos)} 个视频 + {len(notes)} 个图文 = {len(all_links)} 条")
    print(f"[完成] 已保存到: {out.resolve()}")

    # 提示下一步
    print("\n下一步批量下载:")
    print(f'  python "dy.py" -f "{out.name}"')


if __name__ == "__main__":
    main()
