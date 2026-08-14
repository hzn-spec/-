import sys
import io
import os

# 1. 设置Python标准输出为UTF-8（爬中文内容必需，避免乱码）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 2. 如果是Windows，尝试切换终端代码页到UTF-8
if sys.platform == 'win32':
    os.system('chcp 65001 > nul')  # > nul 是隐藏切换成功的提示信息

import re
import requests
from bs4 import BeautifulSoup

# ---------- 完整浏览器请求头（越像真实浏览器越好） ----------
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Connection': 'keep-alive',
}

# ---------- 支持命令行传入任意网址：python Untitled-1.py <网址> ----------
DEFAULT_URL = 'https://urdu.people.cn/n3/2026/0803/c518778-20484455.html'
url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
print('正在抓取:', url)

# ---------- 请求网页：用 Session 发送请求 ----------
session = requests.Session()
session.headers.update(HEADERS)

response = session.get(url, timeout=15)
response.raise_for_status()
# 自动判断编码，避免中文页面乱码
if not response.encoding or response.encoding.lower() == 'iso-8859-1':
    response.encoding = response.apparent_encoding
soup = BeautifulSoup(response.text, 'lxml')


# =================== 通用（新闻站）提取函数 ===================
def get_title(soup):
    """提取标题：优先 <h1>，其次 meta og:title，最后 <title> 标签"""
    h1 = soup.find('h1')
    if h1:
        t = h1.get_text(strip=True)
        if t:
            return t
    og = soup.find('meta', property='og:title')
    if og and og.get('content'):
        return og['content'].strip()
    return soup.find('title').get_text(strip=True)


def get_time(soup):
    """提取发布时间：优先 meta 标签，其次常见 class"""
    for meta in soup.find_all('meta'):
        key = (meta.get('property') or meta.get('name') or '').lower()
        if key in ('article:published_time', 'og:updated_time', 'publishdate', 'pubdate'):
            val = meta.get('content')
            if val:
                return val.strip()
    for key, value in [('class', 'pubtime'), ('class', 'box01'), ('class', 'art_info'),
                       ('class', 'info'), ('id', 'pubtime_baidu')]:
        el = soup.find('div', **{key: value})
        if el:
            t = el.get_text(strip=True)
            if t and re.search(r'\d{4}', t):
                return t
    return ''


def get_content_div(soup):
    """提取正文容器：依次尝试常见选择器，最后用文本密度启发式兜底"""
    candidates = [
        ('id', 'rwb_zw'),          # 中文人民网正文
        ('class', 'rm_txt_con'),   # 中文人民网观点频道正文
        ('class', 'article'),      # 英文人民网正文
        ('id', 'p_content'),
        ('class', 'cw_content'),
        ('class', 'content'),
        ('class', 'main-content'),
        ('class', 'article-content'),
        ('class', 'post-content'),
        ('class', 'TRS_Editor'),
        ('id', 'ozoom'),
    ]
    for key, value in candidates:
        el = soup.find('div', **{key: value})
        if el and el.find('p'):
            return el
    # 兜底：找包含正文文本最多的“叶子”div（不含子 div）
    best, best_len = None, 0
    for div in soup.find_all('div'):
        if div.find('div'):
            continue
        text = div.get_text('', strip=True)
        if len(text) > best_len:
            best_len, best = len(text), div
    return best if best else soup


def keep_para(text):
    """判断段落是否保留（过滤导航、图片来源等干扰文本）"""
    if not text:
        return False
    if text.startswith('(Web editor') or text.startswith('(责编') or text.startswith('(责编：'):
        return False
    if 'Related Stories' in text or 'Languages' in text:
        return False
    if re.fullmatch(r'[=*_\-—·\s]{5,}', text):  # 纯分隔线
        return False
    return True


def extract_news(soup):
    """新闻站：提取标题、时间、正文"""
    title = get_title(soup)
    pub_time = get_time(soup)
    content_div = get_content_div(soup)
    body = [p.get_text(strip=True) for p in content_div.find_all('p')]
    body = [t for t in body if keep_para(t)]
    # 正文过少说明容器可能选错，回退到整页所有 <p>
    if len(body) < 3:
        body = [p.get_text(strip=True) for p in soup.find_all('p')]
        body = [t for t in body if keep_para(t)]
    return title, pub_time, body


# =================== 主流程 ===================
title, pub_time, body = extract_news(soup)

print('标题:', title)
if pub_time:
    print('发布时间:', pub_time)

if body:
    print('\n' + '=' * 60)
    print('正文:')
    print('=' * 60)
    for para in body:
        print(para)
        print()

    # ---------- 保存到文件（文件名按网址中的数字ID自动生成） ----------
    m = re.search(r'(\d{6,})', url)
    out_name = ('output_' + m.group(1) + '.txt') if m else 'output.txt'
    with open(out_name, 'w', encoding='utf-8') as f:
        f.write('网址: ' + url + '\n')
        f.write('标题: ' + title + '\n')
        if pub_time:
            f.write('发布时间: ' + pub_time + '\n')
        f.write('\n' + '=' * 60 + '\n')
        f.write('正文:\n')
        f.write('=' * 60 + '\n')
        for para in body:
            f.write(para + '\n\n')
    print('\n已将内容保存到', out_name)
else:
    print('未找到正文内容，请检查网页结构是否变化。')