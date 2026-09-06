# `nhk-easy-fetcher` 开发规格

> 状态：v0.1，文档优先。最后依据公开响应与参考代码核验：2026-09-05。

## 1. 项目目标与成功标准

`nhk-easy-fetcher` 是一个**本地优先的个人学习工具**。它在用户自己的机器上发现 NHK NEWS WEB EASY 的近期文章，取得在该会话中合法可访问的文章文本和音频，并生成适合离线复习的文件。它不提供网站、不托管共享内容，也不把 NHK 的页面当成稳定公开 API。

v0 成功的定义：在一次普通的本地运行中，工具能（1）发现给定日期或最近一段时间的文章 URL；（2）明确判断页面是否包含完整经典正文；（3）为完整文章输出可追溯的 JSON、Markdown、TXT，必要时保存源 HTML；（4）在有有效音频元数据且本机有 `ffmpeg` 时生成可播放的 M4A；（5）安全地跳过已完成项目并留下可诊断的运行记录。

### 范围内

- Python CLI、可复用库和本地 SQLite 状态；
- sitemap 文章发现、页面获取、HTML/ruby 解析、HLS 音频下载；
- 本地文件导出、缓存、去重、重试和调度入口；
- 用合成夹具覆盖外部页面结构变化的单元/契约测试；
- GitHub Actions 的代码质量和无内容持久化的站点契约检查。

### 明确不在范围内

- 公开 API、Web UI、用户账户、云同步、SaaS、多人共享；
- 把全文、图片或音频提交到 Git、Release、Pages、Actions artifact 或其他公开位置；
- 绕过 NHK ONE 登录、同意、地区、付费、反爬或速率限制；
- 自动翻译、Anki 生成、词典/LLM 功能（可在稳定后作为独立插件讨论）；
- 用数据库服务、Docker、队列等基础设施解决本地单用户问题。

## 2. 设计原则

1. **来源可验证，结果可追溯。** 每个导出记录源 URL、抓取时间、解析模式、内容哈希和工具版本。
2. **完整性优先于“看起来成功”。** 只有存在 `#js-article-body` 的经典 EASY 响应才标为 `complete`；截断 Next.js 页面默认不输出为完整文章。
3. **授权是易变适配器。** 授权 Cookie、跳转和 header 绝不散落于 crawler、parser 和 audio 模块中。
4. **最少请求。** sitemap、状态库、缓存和限流先于并发；遇到 401/403/429 时宁可停下，也不扩大请求强度。
5. **本地内容不进入仓库。** `.gitignore` 覆盖输出、Cookie、SQLite 和本地配置；CI 不发布抓取结果。
6. **不把 HTTP 200 当成功。** 成功必须同时满足 HTTP、页面模式、内容选择器、结构校验和所请求 artifact 的校验。

## 3. 推荐技术栈

| 层 | 选择 | 原因 |
| --- | --- | --- |
| Runtime | Python 3.11+ | 跨平台、适合轻量 CLI 与学习数据处理。 |
| HTTP/Cookie | `httpx` | async/同步均可、timeout 和显式 cookie 管理清晰。 |
| HTML | `selectolax` 或 `lxml` | 对 selector、`ruby`、属性清洗有稳定支持；实现时二选一，不同时引入。 |
| CLI | `Typer` + `Rich` | 子命令、类型校验和人类可读输出。 |
| 模型/配置 | `pydantic` + `tomllib` | 明确 schema 与 Python 标准 TOML 读取。 |
| 本地状态 | 标准库 `sqlite3` | 单文件、事务和唯一索引足够。 |
| 音频 | 系统 `ffmpeg` | 正确处理 HLS、优先 remux 而非转码。 |
| 测试 | `pytest`、`respx`、`pytest-cov` | 可离线 mock HTTP，避免 CI 反复抓取 NHK。 |
| 质量 | `ruff`、`mypy` | 快速静态质量门槛。 |

如果开发者更熟悉 `BeautifulSoup`，可以替代 HTML 库；但 parser API 必须保持独立，不能把库对象泄露到业务模型中。

## 4. 总体架构

```text
Typer CLI / scheduler entry point
              |
              v
        FetchApplication
   +----------+-----------+---------------------------+
   |          |           |                           |
   v          v           v                           v
Discovery  AuthProvider  ArticleFetcher            StateStore
 sitemap    volatile      HTTP + retry              SQLite
 list page   session           |                       |
   |             |             v                       |
   +----------> HTTP Session -> PageModeDetector -------+
                              |                         |
                    +---------+---------+               |
                    v                   v               |
                 Parser            AudioResolver         |
                    |                 |                 |
                    +-------> Exporter +--> output tree  |
                                                   ^      |
                                                   +------+
```

### 模块职责

| 模块 | 只负责什么 | 不能负责什么 |
| --- | --- | --- |
| `discovery.py` | 读取 sitemap、过滤文章 URL、按日期/窗口排序。 | 获取正文或推断音频地址。 |
| `auth.py` | 创建、刷新、序列化短期的授权会话；隐藏 Cookie 细节。 | 解析文章、存储文章内容。 |
| `client.py` | timeout、UA、cookie jar、限流、retry、响应记录。 | 把非 200 伪装成空文章。 |
| `page_mode.py` | 将响应判为 `classic_complete`、`next_partial` 或 `unknown`。 | 提取业务字段。 |
| `parser.py` | 从经典 HTML 提取标题、日期、段落、ruby、图片。 | 发网络请求。 |
| `audio.py` | 从已经取得的元数据解析 HLS URL、调用 `ffmpeg`、验证输出。 | 根据文章 ID 猜 URL。 |
| `storage.py` | 原子写文件、路径命名、SHA-256、SQLite 事务。 | 重新抓取已验证完成的内容。 |
| `app.py` | 编排一次 fetch run 和运行摘要。 | 了解 HTML selector 或 Cookie 名称。 |

## 5. 数据流

```text
1. CLI: nhk-easy fetch --date today --audio
2. Config: 读取本地 TOML，解析时区、速率、输出和授权模式
3. Discovery: GET public sitemap -> 保留 /easy/ne.../ne....html
4. StateStore: 去掉已经 complete 且 schema/source-hash 未变的文章
5. AuthProvider: 获得或刷新有效的浏览器兼容会话（若需要）
6. ArticleFetcher: 用同一会话 GET 文章页 -> PageModeDetector
7. Parser: classic_complete -> 标准 ArticleRecord；partial -> 显式错误/可选 partial
8. Metadata: 若授权 metadata 提供 news_easy_voice_uri，解析 HLS URL
9. Audio: ffmpeg 下载至临时文件 -> 校验 -> 原子改名为 audio.m4a
10. Exporter: 原子写 JSON / MD / TXT / 可选 HTML，更新 SHA-256
11. StateStore: 单一事务把 article/artifact/run 写为 complete
12. CLI: 输出成功、跳过、partial、失败的结构化摘要和退出码
```

任意一步失败时，不把半成品升级为 `complete`。已写出的临时文件保留 `.partial` 扩展名，并可由 `nhk-easy cleanup` 或下一次同一 article run 安全清除。

## 6. NHK NEWS WEB EASY 的当前抓取方式

### 6.1 已观察到的入口

截至本文日期，`https://news.web.nhk/news/easy/sitemap/sitemap.xml` 可匿名读取，且含两类 URL：介绍/灾害说明页和形如 `https://news.web.nhk/news/easy/neYYYY.../neYYYY....html` 的新闻文章页。因此 v0 将 sitemap 作为**文章发现主入口**，而不是依赖首页 HTML。

同日的裸请求 `https://news.web.nhk/news/easy/top-list.json` 返回 HTTP 401。这意味着它不是适合匿名 crawler 的固定 API；只有当 `AuthProvider` 已建立有效授权会话时，才可把它作为标题、预定发布时间、封面或音频元数据的补充来源。实现不能假设其字段、路径或授权要求永远不变。

### 6.2 页面模式

同一 article URL 可能有两种不同响应：

| 模式 | 典型特征 | 默认处理 |
| --- | --- | --- |
| `classic_complete` | `.article-title`、`.article-date`、`#js-article-body`，正文含 `<p>` 及可能的 `<ruby><rt>`。 | 允许完整解析和导出。 |
| `next_partial` | Next.js 脚本/页面外壳及 NHK ONE 使用确认内容，缺少 `#js-article-body`。 | 返回 `FullContentUnavailable`；不写成 complete。 |
| `unknown` | 登录页、验证码、异常 HTML、维护页或 selector 漂移。 | 保留最小诊断信息，停止该项并请求人工复核。 |

`PageModeDetector` 的优先级是 selector，而不是标题文本或 HTTP status。每次站点契约测试都必须至少验证：经典 fixture 识别为 complete、未经授权 fixture 绝不被误判为 complete、未知页面 fail closed。

### 6.3 推荐发现与补充元数据策略

1. 从 sitemap 收集和规范化文章 URL；
2. 用文章 ID（URL 中同名目录/文件段）作为稳定的本地身份候选；
3. 仅在有效授权会话中尝试当前 `top-list.json` 或等效 metadata；
4. 以页面的标题、日期和正文为完整正文的权威来源；
5. 以 metadata 中明确给出的 voice URI 为音频的权威来源；
6. 当 metadata 不可用时，正文可独立导出，音频状态记为 `unavailable`，而不是猜测 URL。

## 7. 认证与匿名授权注意事项

NHK ONE 的匿名授权状态是当前最脆弱的外部依赖。本项目把它当作**站点访问上下文**，而不是用户账户登录，也不把第三方反向工程结果等同于官方接口。

### 必须遵守的约束

- 不要求用户提供 NHK 密码，也不保存密码、浏览器主 profile 或长期可识别凭据；
- 只在用户可以正常使用该页面的地区、网络和服务条款范围内工作；不得伪造地区、邮编、身份、同意状态或绕过显式同意页面；
- 每个授权请求只使用浏览器正常需要的最少 header；不探测、枚举或暴力刷新 token；
- Cookie/token 只保存在受限权限的本地 cache（建议 `0700` 目录、`0600` 文件），日志中只记录名称/过期时间/哈希，绝不记录值；
- 401/403、重定向到确认页、Cookie 过期或 selector 不匹配时，停止自动重试并返回 `authorization_required` 或 `authorization_changed`；
- 默认不使用 `--allow-partial`。用户若显式开启，导出文件必须有 `content_status: partial`，并在 Markdown 顶部明显标注。

### `AuthProvider` 接口

```python
class AuthProvider(Protocol):
    def get_session(self, now: datetime) -> AuthorizedSession:
        """Return an unexpired session or raise AuthorizationUnavailable."""

    def invalidate(self, reason: str) -> None:
        """Discard only the local cached session; never retry aggressively."""
```

v0 先实现三个 provider：`NoAuthProvider`（适合 sitemap/未受限请求）、`CookieJarProvider`（用户在合规前提下提供的独立 Cookie 文件）和 `FixtureAuthProvider`（离线测试）。若后来验证存在可合规、无身份伪造的匿名页面授权流程，再加入独立的 `BrowserCompatibleAnonymousProvider`；它必须有专门 live contract test、feature flag 和变更日志。

## 8. 正文解析与 furigana

### 8.1 解析规则

从 `classic_complete` HTML 提取：

- 标题：`.article-title` 的文本，以及保留 ruby 的 HTML 版本；
- 日期：`.article-date`，再规范为 ISO 8601 / `Asia/Tokyo`；
- 正文：`#js-article-body` 的直接段落顺序；
- 图片：优先 `meta[property="og:image"]`，不存在则 `null`；
- 链接：保留可读文字；正文导出时删除或中和外链 `href`，不执行脚本；
- 属性/元素：删除 `script`、`style`、事件属性、嵌入式追踪内容；保留语义 `p`、`br`、`ruby`、`rt`、`rp`、`em`、`strong`。

不要用单个正则表达式解析整页 HTML。解析前后应记录 selector/version，以便站点改版时定位差异。

### 8.2 双文本模型

`ArticleRecord` 同时保留：

- `body_html_with_ruby`：已清洗但保留 `<ruby>` 的语义 HTML；
- `body_text_with_readings`：例如 `漢字（かんじ）` 的学习文本；
- `body_text_plain`：删除 `rt`/`rp` 后的自然正文；
- `paragraphs`：按段保存这三个视图，避免后续导出再猜断行。

对 `<ruby>漢字<rt>かんじ</rt></ruby>`：plain 视图为 `漢字`，readings 视图为 `漢字（かんじ）`。`rp` 只用于兼容括号，不能混进 plain 文本。标题和摘要遵循同一规则。

### 8.3 完整性校验

至少要求：非空标题、非空正文段落、正文节点数与导出段落数一致、HTML 没有残留脚本、Markdown 无空标题。若 title/URL 的文章 ID 不一致、正文只有确认页文本、解析后内容长度异常小，写入 `parse_suspect` 而非成功。

## 9. 音频与 HLS 下载

音频必须以授权 metadata 明确提供的 `news_easy_voice_uri` 为准。参考实现显示当前音频形式类似：

```text
news_easy_voice_uri
  -> remove file extension
  -> https://vod-stream.nhk.jp/news/easy_audio/{voice-stem}/index.m3u8
```

这只是当前的适配假设，必须在每个真实 article 上校验；**不得**退回到旧项目按 `news_id` 拼接 Akamai 路径的方式。`AudioResolver` 输出的对象应含 `manifest_url`、来源字段、解析时间和可用性状态。

### 下载策略

1. 先请求 manifest，确认是 HLS playlist 而不是 HTML 错误页；
2. 传入所需 Referer/cookie 的最小集；不要将 token 复制到命令行日志；
3. 调用 `ffmpeg` 写入临时 M4A；优先 `-c copy`，避免无谓从 AAC 转 MP3；
4. 用 `ffprobe` 验证文件有音频流、时长大于零、容器可读；
5. 原子 rename 成 `audio.m4a`，并写入 artifact hash；
6. 若 HLS 是受保护、无声、过期或不允许 remux，报告 `audio_unavailable`，不尝试绕过 DRM/签名/地域限制。

计划接口：

```console
nhk-easy fetch --audio=off                 # 仅正文
nhk-easy fetch --audio=m4a                 # 默认，需要 ffmpeg
nhk-easy fetch --audio=manifest --keep-manifest
nhk-easy verify --audio path/to/audio.m4a
```

MP3 只能作为显式兼容选项 `--audio=mp3`，并在 metadata 写明它是重新编码的派生文件。

## 10. 文件格式与输出树

默认 `--output ~/NHK-Easy`。同一文章所有 artifact 放在专属目录，避免日文标题成为文件名和跨日期碰撞。

```text
~/NHK-Easy/
├── .nhk-easy-fetcher/
│   ├── state.sqlite3
│   ├── auth/                  # 权限受限，不进 Git
│   ├── cache/
│   └── runs/
└── articles/
    └── 2026/
        └── 2026-09/
            └── 2026-09-05_ne2026090512345/
                ├── article.json
                ├── article.md
                ├── article.txt
                ├── article.html            # 仅 --format html
                ├── audio.m4a               # 仅 --audio m4a
                └── checksums.sha256
```

`article.json` 是唯一完整的机器可读源，schema 版本从 `1` 起：

```json
{
  "schema_version": 1,
  "article_id": "ne2026090512345",
  "source_url": "https://news.web.nhk/news/easy/ne.../ne....html",
  "fetched_at": "2026-09-05T01:23:45Z",
  "published_at": "2026-09-05T10:00:00+09:00",
  "content_status": "complete",
  "parser_mode": "classic_complete",
  "title": {"plain": "...", "with_readings": "...", "html_with_ruby": "..."},
  "paragraphs": [],
  "audio": {"status": "available", "format": "m4a", "sha256": "..."},
  "provenance": {"tool_version": "0.1.0", "source_contract_version": 1}
}
```

Markdown 顶部必须含来源链接、发布时间、抓取时间、`content_status` 和“NHK content — personal study only, do not redistribute”的短提示。TXT 默认用 plain 文本；`--furigana=readings` 才用括号读音。HTML 是经过清洗的导出，不是原始 HTTP response。

## 11. CLI 设计

```console
nhk-easy fetch [--date YYYY-MM-DD|today] [--since YYYY-MM-DD]
               [--output PATH] [--format markdown,json,text,html]
               [--furigana plain|readings|both]
               [--audio off|m4a|mp3|manifest]
               [--max-articles N] [--dry-run] [--allow-partial]

nhk-easy status [--output PATH] [--json]
nhk-easy verify [ARTICLE_DIR|--output PATH] [--audio]
nhk-easy cleanup [--output PATH] [--older-than DAYS] [--dry-run]
nhk-easy probe [--live] [--json]
```

| 退出码 | 意义 |
| --- | --- |
| `0` | 所有请求项 complete 或已跳过。 |
| `1` | 可重试的运行错误（网络/5xx/临时 ffmpeg 失败）。 |
| `2` | 使用或配置错误。 |
| `3` | 授权不可用/过期/改变，未获取完整内容。 |
| `4` | 站点契约改变或解析不可信。 |
| `5` | 部分成功：有完成项，也有失败/partial 项。 |

默认人类输出给出总数和每个失败类别；`--json` 输出不含 Cookie、authorization header、全文或预签名 URL 的结构化运行摘要。

## 12. 目录结构

目标代码结构（尚未创建）：

```text
src/nhk_easy_fetcher/
├── __init__.py
├── cli.py
├── app.py
├── config.py
├── models.py
├── errors.py
├── client.py
├── auth.py
├── discovery.py
├── page_mode.py
├── parser.py
├── audio.py
├── storage.py
└── diagnostics.py
tests/
├── fixtures/                  # synthetic/minimized; no raw NHK corpus
├── test_config.py
├── test_discovery.py
├── test_page_mode.py
├── test_parser.py
├── test_auth.py
├── test_audio.py
├── test_storage.py
├── test_app.py
└── test_cli.py
.github/workflows/
├── ci.yml
└── source-contract.yml
```

不要在 v0 创建 `api/`、`web/`、`database/`、Docker 或 background daemon 目录。一个可由系统调度器调用的命令比常驻服务更可靠、更易审计。

## 13. 配置

首次运行可由 `nhk-easy init` 生成用户目录中的 TOML，不把真实路径或 Cookie 文件放进仓库：

```toml
[fetch]
timezone = "Asia/Tokyo"
request_timeout_seconds = 20
max_retries = 2
min_interval_seconds = 1.5
max_concurrency = 1
allow_partial = false

[discovery]
sitemap_url = "https://news.web.nhk/news/easy/sitemap/sitemap.xml"
lookback_days = 3

[auth]
provider = "none"              # none | cookie_jar | browser_compatible_anonymous
cookie_jar_path = "~/.nhk-easy-fetcher/auth/cookies.json"
cache_until_expiry = true

[audio]
mode = "off"                   # off | m4a | mp3 | manifest
ffmpeg_path = "ffmpeg"
keep_manifest = false

[storage]
output_dir = "~/NHK-Easy"
keep_raw_response = false
```

实现须拒绝未知配置键或给出警告；支持 `NHK_EASY_CONFIG` 指定配置路径，支持 `NHK_EASY_FFMPEG_PATH` 覆盖可执行文件。不要用环境变量传递原始 Cookie 值；只允许受权限保护的文件路径。

## 14. 错误处理、限流与可观测性

### 分类与动作

| 类别 | 例子 | 动作 |
| --- | --- | --- |
| `NetworkTransient` | timeout、DNS、连接中断 | 指数退避 + jitter，最多 2 次。 |
| `RemoteTransient` | 502/503/504 | 最多 2 次，保持单并发。 |
| `RateLimited` | 429 / `Retry-After` | 等待 `Retry-After`（上限可配）；无值则停止 run。 |
| `AuthorizationUnavailable` | 401/403、确认页、Cookie 过期 | 失效本地 session，一次受控刷新；仍失败则退出码 3。 |
| `SourceContractChanged` | 选择器缺失、HTML 是异常页、metadata schema 不匹配 | 不猜测，不重试，退出码 4。 |
| `AudioUnavailable` | 没有 voice URI、manifest/ffmpeg 校验失败 | 保留合格正文，audio 状态失败，run 为部分成功。 |
| `LocalWriteFailed` | 磁盘满、权限、哈希不一致 | 保留 `.partial` 和诊断，不能写 DB complete。 |

日志采用 JSON Lines，字段包括 `run_id`、`article_id`、`stage`、`attempt`、`status_code`、`page_mode`、`elapsed_ms` 和错误类。URL 可记录，但 query string、Cookie、令牌、正文、音频 manifest 内容和系统绝对路径在默认日志中脱敏。

## 15. 缓存与去重

SQLite schema 至少包含：

```text
runs(run_id, started_at, finished_at, config_hash, summary_json)
articles(article_id PRIMARY KEY, source_url, published_at, content_status,
         parser_mode, source_fingerprint, completed_at)
artifacts(article_id, kind, path, sha256, bytes, created_at,
          PRIMARY KEY(article_id, kind))
auth_sessions(provider, expires_at, encrypted_or_permission_checked_path,
              last_failure_at)
```

去重键优先为 `article_id`；遇到 URL 变体时用规范 URL 和正文 `source_fingerprint` 辅助。跳过规则是：同 article ID 已有 `complete`、所有请求的 artifact 存在并哈希通过、schema/source-contract 兼容。`--force` 可重新获取，但仍要限流；`--refresh` 只检查 source fingerprint，不删除旧文件。

缓存分两类：短期 sitemap/metadata HTTP cache（带 TTL，不依赖敏感 Cookie）和授权会话 cache（仅到 expiry、严格权限）。不要持久化 raw full HTTP 响应，除非用户显式 `--keep-raw-response`；即使如此也只能在本地输出树中，不得进入 Git。

## 16. 调度与 GitHub Actions

### 本地调度

推荐通过系统自己的调度器运行短命令：macOS 用 `launchd`，Linux 用 systemd timer 或 cron，Windows 用 Task Scheduler。任务应以 `Asia/Tokyo` 业务日期运行，并把 stdout/stderr 写入受限本地日志：

```console
nhk-easy fetch --date today --audio=m4a --output ~/NHK-Easy
```

不内建常驻 daemon。若连续三次因授权或页面契约失败，调度入口应停止后续自动请求并提示人工检查，而不是无限重试。完整示例见 [operations.md](operations.md)。

### GitHub Actions

| 工作流 | 触发 | 内容 |
| --- | --- | --- |
| `ci.yml` | push、pull request | Python matrix、ruff、mypy、pytest、coverage；完全离线。 |
| `source-contract.yml` | `workflow_dispatch`；可选低频 schedule | 只在明确启用的 live mode 下读取 sitemap 和一个最小页面探针；不下载/上传/提交全文或音频。 |
| Release（未来） | tag | 构建 Python 包、签名/attestation、发布代码；绝不包含 NHK artifact。 |

公共 GitHub Actions 不能成为每天抓取并公开保存 NHK 内容的代理。个人自动抓取应在用户自己的受控机器上运行；即便是私有仓库，也应在启用任何 content artifact 前重新核对 NHK 条款。

## 17. 测试策略

### 单元测试（默认离线）

- 配置 schema、未知键、环境变量路径覆盖；
- sitemap XML 的文章过滤、去重、日期窗口和非文章 URL 排除；
- `PageModeDetector` 对 classic/partial/unknown 的 fail-closed 识别；
- ruby 保留、去读音、段落断行、链接/脚本清洗；
- JSON/MD/TXT/HTML 导出稳定性和 schema version；
- SQLite transaction：半成品不可见、重复运行跳过、hash 不一致重抓；
- 重试策略、`Retry-After`、401/403/429/5xx 分类；
- HLS resolver 的 URI stem、manifest content-type 检查、`ffmpeg` 参数和 `ffprobe` 失败处理；
- CLI exit code、`--dry-run` 不写文件、`--allow-partial` 标注。

夹具必须手工构造或最小化为结构性样本，不能把真实 NHK 全文、音频片段或 Cookie 提交到仓库。

### 集成与 live contract 测试

- 默认跳过；仅在 `NHK_EASY_LIVE_TEST=1` 时运行；
- 最多访问 sitemap 和一个公开/有授权的最小 article，串行、低频、无下载持久化；
- 断言 selector/页面模式/HTTP 分类，不断言具体标题或新闻数量；
- 任何授权/live 测试都不能在 fork PR、外部贡献者 PR 或公开日志中运行；
- 站点变化时保存 **结构摘要**（状态码、content-type、selector 布尔值、body hash 前缀），不保存内容本身。

## 18. 法律、版权与使用边界

本项目提供的是工具代码，不授予用户 NHK 内容、音频、图片或 metadata 的任何权利。NHK 的页面和服务规则会变化，用户必须在运行前和持续使用中审阅适用的 NHK Internet Service terms、版权说明和所在地区规则。

项目层面必须坚持：

- 仅限个人、合法、低频的学习用途；
- 不公开镜像、不再发布、不售卖、不将内容训练模型或建立可检索公共库；
- 不移除来源/版权提示，不修改内容以误导来源；
- 不绕过付费、登录、地区、验证码、DRM、速率限制或明确的访问限制；
- 不对 NHK 服务施压：单并发、缓存、退避、失败即停；
- 如 NHK 改变规则、停止访问或要求删除，停止抓取并清除本地内容；
- 代码 MIT 许可与抓取内容的版权完全分离。

README、每个导出 Markdown、CLI `--help` 和 Release 页面都应保持这一边界。对任何商业、课堂大规模分发、服务端存档或 API 提供的需求，必须先取得独立的权利/许可，不属于本项目默认功能。

## 19. 路线图

| 阶段 | 交付物 | 完成门槛 |
| --- | --- | --- |
| 0 — 规格 | 本文、参考来源、测试计划 | 术语、范围和法律边界已固定。 |
| 1 — 离线骨架 | 包、模型、配置、CLI、SQLite、synthetic fixtures | 所有离线测试通过。 |
| 2 — 无授权发现与解析 | sitemap、页面模式、经典页面 parser、导出 | 对 fixture/显式本地 HTML 完整可用。 |
| 3 — 授权适配 | 可替换 provider、session cache、live contract gate | 合规审查后才能启用；变更即安全失败。 |
| 4 — 音频 | metadata resolver、ffmpeg/ffprobe、checksum | 一个合法可用样本端到端验证，不猜 URL。 |
| 5 — 运行可靠性 | retry、状态、cleanup、local scheduler docs | 重跑幂等、失败可解释。`verify`/`cleanup` CLI、`docs/operations.md`、source-contract workflow 已落地。 |
| 6 — 可选扩展 | Anki/RSS/词表/翻译插件 | 需单独设计和权限/版权评估。 |

## 20. 开发前检查清单

- [ ] 重新验证 sitemap、`top-list.json` 和至少一个 article 的页面模式；
- [ ] 记录所有 live 响应的日期、状态码、selector，不保存内容；
- [ ] 检查授权路径是否仍符合 NHK 当前规则；如果不明确，停在无授权/fixture 层；
- [ ] 先写 synthetic fixture 的失败测试，再实现 selector；
- [ ] 保证任何输出、Cookie、SQLite 和 raw response 都在 `.gitignore` 内；
- [ ] 代码/测试均通过后才启用人手触发的最小 live contract test；
- [ ] 在提交 Release 或宣传可用前，复核本文的法律边界和实际行为是否一致。
