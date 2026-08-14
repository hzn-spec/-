# -*- coding: utf-8 -*-
r"""
抖音账号【全部作品】链接提取工具
================================
用 Playwright 打开指定抖音账号主页的"作品"列表（showTab=post），
滚动懒加载抓取全部视频 + 图文笔记链接，保存到 txt 文件。
输出文件可直接交给主程序批量下载：

  python dy.py -f zuopin_links.txt

用法：

  python zuopin.py "https://www.douyin.com/user/MS4wLjABAAAAxxxx"
  python zuopin.py "https://v.douyin.com/xxxxxxx/" -o my_links.txt
  python zuopin.py "https://www.douyin.com/user/xxx" --sub-tab video
  python zuopin.py "https://www.douyin.com/user/xxx" --cookies cookies.txt

参数：
  -o, --output              输出文件（默认 zuopin_links.txt）
  --sub-tab TYPE            子标签：video=仅视频 / image=仅图文 / all=全部(默认)
  --max-scroll N            最大滚动次数（默认 150，作品很多的账号可调大）
  --cookies FILE            抖音 cookies.txt（Netscape 格式，一般不需要，作品列表游客可看）
  --cookies-from-browser B  从浏览器读取登录 cookies（edge/chrome/firefox）

注意：
  * 作品列表 (showTab=post) 游客可看，一般无需 cookies。
  * 抖音主页正确参数是 ?showTab=post（不要用 ?tab=post，前端不识别）。
  * 懒加载关键：鼠标必须移到列表区域上方再 wheel，否则只抓到首屏。
  * 只抓 [data-e2e="user-post-list"] 容器内链接，避免右侧"相关推荐"混入。
  * 需要 playwright + chromium（与主程序同一环境）。
"""
import argparse
import re
import sys
import urllib.request
from pathlib import Path

# PowerShell 下中文输出避免 GBK 编码报错
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

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


# 抖音用户页"作品"列表容器，只抓容器内链接，避免右侧推荐栏内容混入
POST_CONTAINERS = (
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
    """滚动页面触发作品列表懒加载。

    抖音新版主页 body 是 overflow:hidden，真正可滚动的是包含作品列表的
    内容容器（如 .parent-route-container），所以从 user-post-list 向上
    找 scrollHeight > clientHeight 的祖先来滚动；找不到时退回鼠标 wheel。
    """
    ok = page.evaluate("""() => {
      // 从作品列表向上找可滚动祖先
      const list = document.querySelector('[data-e2e="user-post-list"]');
      let cur = list;
      for (let i = 0; i < 8 && cur; i++) {
        if (cur.scrollHeight > cur.clientHeight + 5) {
          cur.scrollTop += 800;
          return true;
        }
        cur = cur.parentElement;
      }
      // 兜底：常见内容容器类名
      const alt = document.querySelector('.parent-route-container, .route-sc');
      if (alt) {
        alt.scrollTop += 800;
        return true;
      }
      return false;
    }""")
    if not ok:
        # 再兜底：鼠标移到列表上方再 wheel（老版页面结构）
        page.mouse.move(960, 600)
        page.mouse.wheel(0, 800)


def extract_links_from_page(page) -> tuple[list[str], list[str]]:
    """从当前页面 DOM 提取作品列表里的视频 /video/ 与图文 /note/ 链接（保持顺序去重）"""
    videos: list[str] = []
    notes: list[str] = []
    seen_v: set[str] = set()
    seen_n: set[str] = set()

    containers = page.locator(", ".join(POST_CONTAINERS))
    if containers.count() > 0:
        # 容器内 a 的 href 是完整 URL（带参数），正则提取 /video|note/ 后的数字 ID
        hrefs = containers.evaluate_all(
            "els => els.flatMap(e => "
            "  [...e.querySelectorAll('a[href*=\"/video/\"], a[href*=\"/note/\"]')]"
            "    .map(a => a.getAttribute('href') || ''))"
        )
    else:
        # 兜底：找不到容器时退回全页面抓取
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


def collect_post_links(sec_uid: str, sub_tab: str, max_scroll: int,
                       extra_cookies: list[dict]) -> tuple[list[str], list[str]]:
    """滚动加载作品列表，返回 (视频链接, 图文链接)"""
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
        # 作品列表正确参数是 ?showTab=post（?tab=post 前端不识别）
        tab_url = f"https://www.douyin.com/user/{sec_uid}?showTab=post"
        if sub_tab and sub_tab != "all":
            tab_url += f"&showSubTab={sub_tab}"
        print(f"[信息] 正在打开: {tab_url}")
        page.goto(tab_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)  # 等待首屏渲染

        # 若 ?showTab= 未生效，尝试点击"作品"tab
        try:
            tab_btn = page.locator(
                '[data-e2e="user-tab-post"], '
                'a:has-text("作品"), [role="tab"]:has-text("作品")'
            ).first
            if tab_btn.count():
                tab_btn.click(timeout=3000)
                page.wait_for_timeout(3000)
        except Exception:
            pass

        # 关闭/移除全屏登录弹窗遮罩（否则会挡住页面导致无法滚动）
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
        description="提取抖音账号【全部作品】的视频/图文链接到文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="抖音账号主页链接或 v.douyin.com 分享短链")
    parser.add_argument("-o", "--output", default="zuopin_links.txt",
                        help="输出文件（默认 zuopin_links.txt）")
    parser.add_argument("--sub-tab", choices=["video", "image", "all"],
                        default="all",
                        help="子标签: video=仅视频 / image=仅图文 / all=全部(默认)")
    parser.add_argument("--max-scroll", type=int, default=150,
                        help="最大滚动次数（默认 150，作品多的账号可调大）")
    parser.add_argument("--cookies", metavar="FILE",
                        help="抖音 cookies.txt（Netscape 格式，一般不需要）")
    parser.add_argument("--cookies-from-browser", metavar="BROWSER",
                        choices=["edge", "chrome", "firefox", "opera", "vivaldi", "brave"],
                        help="从浏览器读取登录 cookies")
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

    # 加载 cookies（可选，作品列表游客可看）
    extra_cookies = []
    if args.cookies:
        extra_cookies = parse_netscape_cookies(args.cookies)
        print(f"[信息] 已加载 cookies 文件: {args.cookies} ({len(extra_cookies)} 条)")
    elif args.cookies_from_browser:
        extra_cookies = load_browser_cookies(args.cookies_from_browser)
        print(f"[信息] 已从 {args.cookies_from_browser} 读取 cookies ({len(extra_cookies)} 条)")

    sub_name = {"video": "仅视频", "image": "仅图文", "all": "全部"}[args.sub_tab]
    print(f"[信息] 账号 {sec_uid}，列表: 作品（{sub_name}），最大滚动: {args.max_scroll}")
    videos, notes = collect_post_links(sec_uid, args.sub_tab,
                                       args.max_scroll, extra_cookies)

    all_links = videos + notes
    if not all_links:
        print("\n[失败] 未提取到任何链接。可能原因:")
        print("  1. 账号不存在或已被封禁")
        print("  2. 该账号没有公开作品")
        print("  3. 网络/风控问题 → 尝试 --cookies-from-browser edge 登录后再试")
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
