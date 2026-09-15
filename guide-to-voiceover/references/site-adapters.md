# 站点适配与抓取排障

`fetch_guide.py` 是通用的，但每个站点的正文容器和图片 URL 规则不同。
这份文档记录已验证的站点和踩过的坑。

---

## 1. 已验证：大神 ds.163.com（网易大神）

**站型**：SPA。静态 HTML 里没有任何正文，必须无头渲染。

实测数据（`https://ds.163.com/article/6957e43c4e32d25fb91cf5dd/`）：

| 阶段 | 结果 |
|---|---|
| 静态 HTTP | 21458 B，正文 16 字符（只有 meta 摘要） |
| 无头 Chrome 渲染 | 452501 B，正文 1362 字符，60 个块，9 张图 |
| 正文容器 | `<article class="c-feed-article__content">`（语义标签自动命中） |
| 抽到的东西 | 51 个文字块 + 9 个图片块，**顺序与原文完全一致** |

**图片 URL 规则**（重要）：
- 页面里 `<img>` 同时带 `src` 和 `data-origin`
- `src` 形如 `https://img.166.net/reunionpub/xxx?imageView&tostatic=0&thumbnail=1500x0`
- `data-origin` 是干净原图：`https://ok.166.net/reunionpub/xxx`
- 脚本优先取 `data-origin`；退一步会剥掉 `imageView/thumbnail/tostatic` 查询参数
- 正文图 URL 特征：`1_kol_YYYYMMDD_<hash>`；头像特征：带 `thumbnail=100y100`（脚本按此过滤）

**坑**：同一篇文章里头像和正文图都来自 `166.net`，只按域名过滤会混进头像。
脚本用了三条规则过滤，且**缩略图参数要同时查 `src` 原始属性和最终 URL**——
正文图带 `data-origin`（干净 URL），只查最终 URL 会漏掉头像：

1. `data-origin` 优先，但它没有 `thumbnail` 参数，所以得回看 `src` 属性
2. 任一 URL 里 `thumbnail=WxH` 且 max(W,H) ≤200 → 判为头像
3. 实际下载后 <100×100 的丢弃

另外 `srcset`（`<img srcset>` / `<picture><source srcset>`）也认，
取候选里声明宽度最大的那个；`<source>` 标签本身在 `VOID` 里不会被单独访问。

**已知噪声**：`<title>` 是站点通用标题「大神_游戏热爱者兴趣圈_游戏社区」，
不是文章标题。脚本的标题优先级是 **h1 > og:title > title**——
h1 里的「梦幻淘金专栏|牧场篇（上）-基础入门指南」才是对的。

## 2. 无头 Chrome 的两个致命坑（macOS 实测）

### 坑一：`--user-data-dir` 会挂死

```bash
# ❌ 挂死：>180s 无输出，最后被 kill
chrome --headless=new --user-data-dir=$(mktemp -d) --virtual-time-budget=15000 --dump-dom URL

# ✅ 6 秒出 457KB DOM
chrome --headless=new --disable-gpu --no-sandbox --virtual-time-budget=15000 --dump-dom URL
```

指向一个**一次性空目录**时会卡住不返回。所以脚本默认**不传** `--user-data-dir`；
确实需要隔离 profile 时用 `--chrome-profile <已存在的目录>`。

### 坑二：`--run-all-compositor-stages-before-draw` 和 `--virtual-time-budget` 冲突

两个一起用会挂住不出 DOM。脚本里已经去掉了这个 flag，**别加回来**。

### 坑三：macOS 没有 `timeout` 命令

`timeout 60 chrome ...` 会立刻以 exit 127 失败，看起来像"Chrome 没输出"，
实际是命令根本不存在。要么用 `gtimeout`（`brew install coreutils`），
要么在 Python 里用 `subprocess.run(timeout=...)`——脚本用的是后者。

**排障时先确认命令真的跑起来了**，别被 shell 层面的失败误导。

## 3. 站点适配速查

| 站型 | 表现 | 处理 |
|---|---|---|
| 服务端渲染 | 静态 HTML 就有正文 | 直接抓，`--render auto` 会自动跳过渲染 |
| SPA / CSR | 静态 HTML 正文 <400 字符 | `--render auto` 自动上无头 Chrome |
| 强反爬 | 403 / 空内容 | 换 `--chrome` 走真浏览器；或用浏览器"另存为"后走本地文件模式 |
| 正文容器不标准 | 抽到导航/推荐位 | `--selector` 手动指定 |
| 图片懒加载 | `src` 是占位图 | 脚本已依次尝试 `data-origin / data-original / data-src / data-lazy-src / data-actualsrc` |

### 本地/手工兜底（永远可用）

```bash
# 浏览器另存为 HTML 后
python3 scripts/fetch_guide.py page.html --selector article -o out

# 直接粘贴正文
pbpaste | python3 scripts/fetch_guide.py - -o out

# Markdown / txt
python3 scripts/fetch_guide.py guide.md -o out
```

纯文本模式按空行分段，`#` 开头识别为标题。**抓取失败时不要硬刚反爬，
让用户另存或粘贴是最快的路径。**

## 4. 通用容器启发式

`--selector` 不给时按这个顺序自动判断：

1. `<article>` / `<main>`，文本 ≥**80** 字符 → 直接用
2. 否则对 `div / section / td / article`（文本 ≥**60**）打分：
   `score = 文本长度 × (1 − min(链接密度, 1)) − 4 × 链接密度 × 文本长度
            + min(块级子元素数, 60) × 3`
   链接密度惩罚用来排除导航和推荐位（第二项就是它，系数 4 是 5 倍总惩罚）
3. **覆盖度守卫**：若最佳候选的文本不到全文的 60%，且全文 <5000 字符，
   说明这页本身就是一段片段（另存的 `<ul>`/`<table>`），按全文处理——
   否则只会抓到其中的一个 `<li>`
4. 都不行 → 回退整个文档，并**触发兜底闸**：
   - 抽到 **0 字符**（含 `--selector` 命中空容器）→ 报错退出，绝不产出空稿
   - 非用户指定容器且 <80 字符 → 报错退出
   - 用户显式 `--selector` 命中、或输入是纯文本/Markdown/stdin → 不设下限

## 5. 什么时候该放弃自动抓取

- 需要登录才能看全文
- 正文是图片（扫描版攻略）
- 站点返回验证码页

前两种改成**本地文件/粘贴模式**；第三种让用户手动导出。
**不要为了抓一篇文章写站点专用爬虫**——成本远超手工粘贴一次。
