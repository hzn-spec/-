# 抖音视频/图文下载工具使用说明

## 文件说明

| 文件 | 作用 |
|------|------|
| `d:\code\py\dy.py` | 主下载脚本（yt-dlp 核心，图文走 Playwright） |
| `d:\code\py\gen_douyin_cookies.py` | 单独生成 cookies 的脚本（可选） |
| `d:\code\py\douyin_cookies.txt` | 生成的 cookies 文件（可选，脚本会自动生成） |

## 快速开始

```powershell
# 单链接下载（全自动：自动获取 cookies + 下载）— 支持视频与图文笔记
d:\code\.venv\Scripts\python.exe d:\code\py\dy.py "https://v.douyin.com/xxxxxxx/"

# 多链接
d:\code\.venv\Scripts\python.exe d:\code\py\dy.py "链接1" "链接2"

# 从文件批量（links.txt 每行一个）
d:\code\.venv\Scripts\python.exe d:\code\py\dy.py -f links.txt

# 自定义保存目录
d:\code\.venv\Scripts\python.exe d:\code\py\dy.py "链接" -o D:\video\myfolder
```

视频默认保存到 **`D:\video`**。

## 图文笔记（/note/）说明

抖音图文链接（形如 `https://www.douyin.com/note/xxxxxxxx` 或 `v.douyin.com` 短链）会自动走 **Playwright 渲染页面** 提取轮播图片：

1. 自动解析短链拿到真实帖子 ID
2. 先尝试官方 API（需要有效 cookies）
3. API 被风控时自动降级为 Playwright 无头浏览器打开页面，滚动触发懒加载后提取轮播图片
4. 图片按 `{作者} - {标题} - {序号}.{扩展名}` 命名保存，**签名 URL 原样下载**（去掉 `~tplv-` 模板参数会 403）

注意：图文需要 Playwright + chromium 已安装（见依赖安装）。

## 工作原理（解决抖音反爬）

抖音 2024 年后强制要求请求携带有效 cookies（`ttwid`、`s_v_web_id` 等），否则报错：
```
Fresh cookies (not necessarily logged in) are needed
```

本工具用 **Playwright 无头浏览器** 自动打开抖音页面，让页面 JS 生成签名 cookies，再导出为 Netscape 格式交给 yt-dlp 下载。**全程无需手动操作**。

## 其他用法（可选）

### 1. 使用浏览器 cookies（免 Playwright）
```powershell
# 从 Chrome/Edge/Firefox 读取 cookies
# 注意：Chrome/Edge 需先完全关闭浏览器进程
python d:\code\py\dy "链接" --cookies-from-browser edge
python d:\code\py\dy "链接" --cookies-from-browser chrome
```

### 2. 使用 cookies.txt 文件
```powershell
# 用扩展 "Get cookies.txt LOCALLY" 从浏览器导出 cookies.txt
python d:\code\py\dy "链接" --cookies cookies.txt
```

### 3. 单独生成 cookies
```powershell
python d:\code\py\gen_douyin_cookies.py
# 生成 d:\code\py\douyin_cookies.txt
```

## 故障排查

| 问题 | 解决 |
|------|------|
| 下载中断、连接重置 | 脚本已禁用代理（`proxy: ""`），若你的代理软件开启则需手动配置 |
| 代理端口 7890 错误 | 系统残留的 Clash 代理地址，脚本已自动绕过 |
| 报错需要 cookies | 检查 playwright 是否安装：`pip install playwright && playwright install chromium` |
| 部分视频失败 | 视频可能已被删除/下架，属正常情况 |

## 依赖安装（首次）

```powershell
d:/code/.venv/Scripts/python.exe -m pip install yt-dlp playwright requests
d:/code/.venv/Scripts/python.exe -m playwright install chromium
```
