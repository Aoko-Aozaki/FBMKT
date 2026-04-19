# FB Marketplace Deal Alert

一个面向 Facebook Marketplace 的定时监控工具：按 `watchlist.yaml` 中的关键词搜索商品，筛掉历史上已经见过且价格没有下降的 listing，用语义匹配确认和目标商品相关，再结合大模型判断是否值得追，然后通过 Telegram 发送提醒。

当前实现是一个可长期运行的本地脚本服务，不是一次性爬虫。程序启动后会立即执行一轮检查，之后按固定间隔持续运行。

## 现在这份代码能做什么

- 按 watchlist 中的关键词逐个抓取 Facebook Marketplace 搜索结果
- 使用 `sentence-transformers` 做标题语义匹配，减少 Facebook 搜索噪音
- 用 `max_price` 做硬过滤，超价商品不进入 LLM 判断
- 进入详情页补抓描述和成色信息
- 用 OpenAI Python SDK 调用 DeepSeek 兼容接口，对二手商品做“是否值得追”判断
- 仅对“新 listing”或“价格下降”的 listing 继续处理，避免重复通知
- 将运行状态持久化到 `seen_listings.json`，并按 `STALE_DAYS` 清理过期记录
- 通过 Telegram Bot 发送提醒；如果 Facebook 登录态失效，也会主动发告警
- 每个关键词抓取前加入随机延迟，降低高频访问风险

## 项目结构

```text
.
├── main.py                    # 程序入口，调用调度器
├── login.py                   # 手动登录 Facebook，保存 auth_state.json
├── watchlist.yaml             # 监控关键词和价格策略
├── src/
│   ├── config.py              # .env 和 watchlist 加载
│   ├── models.py              # 数据模型
│   ├── scraper/scraper.py     # Playwright + BeautifulSoup 抓取
│   ├── matcher/matcher.py     # sentence-transformers 匹配
│   ├── llm/evaluator.py       # LLM 评估
│   ├── notifier/notifier.py   # Telegram 推送
│   ├── state/state.py         # seen_listings.json 读写
│   └── pipeline/scheduler.py  # 调度与主流程
├── tests/                     # 单元测试与少量 integration 测试
└── docs/                      # 设计与任务记录
```

## 工作流

```text
启动调度器
  ↓
立即执行一次 run_pipeline()
  ↓
按 watchlist 逐个关键词搜索 Marketplace
  ↓
只保留新 listing 或价格下降的 listing
  ↓
标题与 watchlist 做语义匹配
  ↓
价格高于 max_price 直接跳过
  ↓
抓取详情页描述与 condition
  ↓
LLM 判断 worth_buying / confidence
  ↓
满足阈值则发送 Telegram 提醒
  ↓
保存并清理 seen_listings.json
```

## 运行前准备

- Python 3.11+
- 一个可正常访问 Facebook Marketplace 的账号
- Telegram Bot Token 与 Chat ID
- DeepSeek API Key
- 本机已安装 Playwright Chromium 浏览器

说明：

- 代码中的环境变量名仍然是 `OPENAI_API_KEY`，但当前实现实际使用的是 OpenAI SDK 的兼容模式，`base_url` 指向 `https://api.deepseek.com/v1`，模型名是 `deepseek-chat`
- `sentence-transformers` 的模型在首次真正加载时可能需要下载，第一次运行会比后续慢

## 安装

```bash
pip install -e ".[dev]"
playwright install chromium
```

## 配置

### 1. 创建 `.env`

可以参考仓库里的 `.env.example`，但下面这份示例以当前代码的真实配置项和默认值为准：

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
OPENAI_API_KEY=your_deepseek_api_key
FB_LOCATION=San Francisco, CA

POLL_INTERVAL_MIN=15
SIMILARITY_THRESHOLD=0.60
CONFIDENCE_THRESHOLD=0.70
SCRAPE_DELAY_MIN=10
SCRAPE_DELAY_MAX=30
STALE_DAYS=30
LOG_LEVEL=INFO

AUTH_STATE_PATH=auth_state.json
SEEN_LISTINGS_PATH=seen_listings.json
WATCHLIST_PATH=watchlist.yaml

HEADLESS=1
```

各项含义如下：

- `TELEGRAM_BOT_TOKEN`：Telegram Bot Token，必填
- `TELEGRAM_CHAT_ID`：接收告警的 chat id，必填
- `OPENAI_API_KEY`：当前代码实际拿它作为 DeepSeek 兼容接口的 API Key，必填
- `FB_LOCATION`：Facebook Marketplace 城市位置，必填，例如 `San Francisco, CA`
- `POLL_INTERVAL_MIN`：调度间隔，默认 `15`。代码会对低于 15 分钟的配置打 warning
- `SIMILARITY_THRESHOLD`：标题与 watchlist 的最低匹配阈值，默认 `0.60`
- `CONFIDENCE_THRESHOLD`：LLM 判断“值得追”时要求的最低置信度，默认 `0.70`
- `SCRAPE_DELAY_MIN` / `SCRAPE_DELAY_MAX`：不同关键词之间的随机等待秒数，默认 `10` 到 `30`
- `STALE_DAYS`：状态文件中过期 listing 的保留天数，默认 `30`
- `LOG_LEVEL`：日志等级，默认 `INFO`
- `AUTH_STATE_PATH`：Facebook 登录态文件路径，默认 `auth_state.json`
- `SEEN_LISTINGS_PATH`：运行状态文件路径，默认 `seen_listings.json`
- `WATCHLIST_PATH`：监控列表路径，默认 `watchlist.yaml`
- `HEADLESS`：运行抓取时是否无头模式，`1` 为无头，`0` 为显示浏览器，默认 `1`

### 2. 编辑 `watchlist.yaml`

当前代码支持的字段如下：

- `keyword`：搜索关键词，必填
- `fair_price`：你心里可接受的“合理价”，必填
- `max_price`：硬上限，超过这个价格直接跳过，不调用 LLM；不写时等价于无限大
- `notes`：额外说明，会原样带入 LLM 提示词

示例：

```yaml
watchlist:
  - keyword: "iPhone 14 Pro"
    fair_price: 400
    max_price: 500
    notes: "256GB or above, good condition"

  - keyword: "Herman Miller Aeron"
    fair_price: 250
    max_price: 400
    notes: "any size"
```

## 首次登录 Facebook

运行：

```bash
python login.py
```

脚本会打开一个可见浏览器窗口并进入 Facebook Marketplace。你需要：

1. 在浏览器里手动登录 Facebook
2. 回到终端按一次回车
3. 让脚本把登录态保存到 `auth_state.json`

这个文件包含登录 cookie，不要提交到版本库。

## 启动程序

```bash
python main.py
```

实际行为：

- 程序启动后立刻执行第一轮检查
- 之后每隔 `POLL_INTERVAL_MIN` 分钟执行一次
- 模型、配置和 API client 会在进程内复用，不会每轮都重新初始化

## 通知触发条件

只有同时满足下面几条时才会发送 Telegram 提醒：

- listing 是新出现的，或者价格比历史记录更低
- 标题能匹配到 watchlist 中的某个目标
- `price <= max_price`
- LLM 返回 `worth_buying = true`
- `confidence >= CONFIDENCE_THRESHOLD`

## 运行时文件

- `auth_state.json`：Facebook 登录态，由 `python login.py` 生成
- `seen_listings.json`：去重与价格历史状态，首次运行后自动创建
- `/tmp/fb_search_debug.html`：搜索页抓到了 HTML 但没有成功解析出 listing 时写入
- `/tmp/fb_detail_debug.html`：首次抓详情页时写入，便于排查 Facebook DOM 变化



## 常见问题

### 1. 启动后马上报 `auth_state.json not found`

还没有先执行 `python login.py`，或者 `AUTH_STATE_PATH` 指向了错误路径。

### 2. Telegram 没收到提醒

优先检查以下几项：

- `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID` 是否正确
- 当前 listing 是否真的满足 `worth_buying` 和 `CONFIDENCE_THRESHOLD`
- listing 是否已经在 `seen_listings.json` 中出现过且价格没有下降

### 3. 想看抓取过程中的浏览器

将 `.env` 里的 `HEADLESS=0`，然后重新运行 `python main.py`。

### 4. Facebook 登录失效了怎么办

程序会在检测到跳转到登录页时发送 Telegram 文本告警。收到后重新执行：

```bash
python login.py
```

## 注意事项

- `POLL_INTERVAL_MIN` 虽然可以设得更低，但低于 15 分钟会增加风控风险
- Facebook Marketplace 页面结构经常变化；如果突然抓不到标题、描述或详情页字段，优先检查 `/tmp` 下的调试 HTML
- 当前项目名称和部分变量名沿用了 `openai` 命名，但实际请求链路是 DeepSeek 兼容接口
