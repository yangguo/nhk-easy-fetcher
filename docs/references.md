# 调研记录与参考项目

最后核验：2026-09-05。此文件记录的是实现决策的证据和借鉴边界，并不把任何第三方项目或未公开接口当作官方承诺。

## 当前站点观察

| 项目 | 观察结果 | 对设计的含义 |
| --- | --- | --- |
| [`/news/easy/sitemap/sitemap.xml`](https://news.web.nhk/news/easy/sitemap/sitemap.xml) | 可匿名读取；其中含文章 URL 和非文章的说明页 URL。 | 以 sitemap 为发现入口，过滤 `neYYYY.../neYYYY....html` 的文章路径。 |
| [`/news/easy/top-list.json`](https://news.web.nhk/news/easy/top-list.json) | 未携带授权状态时返回 `401` 与 JSON 响应。 | 不能将它当作裸请求的公开 API；仅在有效授权会话中作为可选元数据源。 |
| 一篇 2026-08 的文章页 | 无授权时 HTTP `200`，但响应含大量 Next.js/NHK ONE 页面标记，未出现经典 `#js-article-body`。 | HTTP 200 不等于拿到了完整正文；解析器必须识别页面模式。 |
| 历史 HLS URL 规则 | 旧项目按 `news_id` 拼接 Akamai URL；对当前样例并不成立。 | 不要根据文章 ID 猜音频 URL；从授权元数据的 `news_easy_voice_uri` 解析并逐条验证。 |

上述直接观察只说明 2026-09-05 的行为。运行时必须把端点、Cookie 名称、HTML selector 和音频 URL 视为可变的外部契约。

## 建议参考的开源项目

### 1. [kenichikawaguchi/nhk-easy](https://github.com/kenichikawaguchi/nhk-easy)

**适合借鉴：** 一个小型 Python CLI 的边界、输出命名、带/不带 furigana 的文本和 HTML 导出、用 `ffmpeg` 处理 HLS 音频、已下载文件跳过逻辑。

**不要照搬：** 它使用旧的 `www3.nhk.or.jp` 与裸 `top-list.json`，并按文章 ID 推导旧 Akamai 音频 URL。它应是 CLI/输出设计参考，而不是 2026 的站点协议参考。

### 2. [nhk-news-web-easy/nhk-easy-task](https://github.com/nhk-news-web-easy/nhk-easy-task)

**适合借鉴：**

- 把带 Cookie 的 HTTP client 独立出来；
- 抓取 `top-list.json`、按文章 ID 取页、用 `#js-article-body` 解析完整正文；
- 从 `news_easy_voice_uri` 得到 HLS 地址；
- 将抓取、解析、存储、定时任务拆开，并配有测试目录和 CI。

**不要照搬：** 该项目是 Kotlin/Spring/MySQL/Docker 的服务型架构，远重于本项目。其授权实现属于对页面行为的适配，不是正式 API；不能复制其中的地区/同意参数来伪造用户状态或规避服务限制。

### 3. [kongleiwork-art/japanese-news-reading-pwa — NHK EASY integration notes](https://github.com/kongleiwork-art/japanese-news-reading-pwa/blob/main/docs/nhk-easy-api-integration-notes.md)

**适合借鉴：** 2026 年的故障模式和回退思路：sitemap 发现、无授权响应与经典页面的差异、`#js-article-body`、`ruby`/`rt` 去假名、授权 Cookie 缓存、并发限制和解析模式日志。

**使用边界：** 这是一份第三方集成笔记，不是 NHK 规范。这里采用“完整正文失败即明确报错/可选 partial”而不是默认把截断正文当成功的策略。

### 4. [kevin840720/nhk-easy-news-crawler](https://github.com/kevin840720/nhk-easy-news-crawler)

**适合借鉴：** Python 包模块化（crawler/parser/export/config）、测试夹具、服务状态端点和 Docker 运行方式。

**不要照搬：** README 仍以旧域名为中心，并且 Flask + PostgreSQL + Docker 超出本地学习工具 v0 的需要。v0 不应为了“可能以后有 API”而先引入数据库服务。

### 5. [asari-mtr/nhk_gogaku_2026](https://github.com/asari-mtr/nhk_gogaku_2026)

**适合借鉴：** HLS 下载的可观测性、失败重试和面向个人收听的调度经验。

**不在本项目范围：** 该项目面向 NHK 语学节目；NHK NEWS WEB EASY 的文章发现、正文和授权模型不同。不能把其节目 URL/元数据假设套用到 EASY。

## 推荐的借鉴顺序

1. 先以 sitemap 和页面选择器实现无内容持久化的探针；
2. 用 `nhk-easy-task` / PWA 笔记确认授权后的响应契约，但将适配器隔离；
3. 以 `nhk-easy` 的简单 CLI 和输出体验为目标；
4. 仅在 v0 已稳定后评估 crawler 的服务化做法。

## 外部来源不是代码依赖

实现者在复制任何代码前应检查源项目许可证、保留所需版权/许可证声明，并优先重写小而清晰的实现。本仓库不引入或分发 NHK 内容样本；测试夹具必须是合成或经过最小化处理的结构性样本。
