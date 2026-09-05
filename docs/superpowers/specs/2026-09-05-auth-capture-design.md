# 一键自动保存 Cookie 设计（2026-09-05）

> 目标：`./scripts/fetch-latest.sh` 缺 cookie 时，弹本机已装 Chrome 供用户点一次同意，回车即自动落盘 `cookies.json`，无须手工导出、无须新装浏览器。

## 1. 架构与组件（已确认）

- 新增 `src/nhk_easy_fetcher/auth_capture.py`：唯一懂 Playwright 的模块；`auth.py` 的 `CookieJarProvider` 不变，只负责读受限文件。
- 新增 CLI：`nhk-easy auth capture [--cookie-jar PATH] [--timeout SEC]`（默认超时 300 秒），复用现有 Typer/Rich 与退出码（成功 0，无效/取消/超时 3，Chrome 缺失 2）。
- Playwright 为可选依赖（`pip install -e '.[browser]'` 或 lazy import 缺失即提示），一律 `channel="chrome"` 复用本机 Chrome，不下载 Chromium。
- 临时 `user-data-dir` 经 `tempfile.mkdtemp` 创建并设 `0700`，退出时（成功或失败）一律删除；绝不读取/写入日常 Chrome profile，不处理任何密码。
- 落盘 `~/.nhk-easy-fetcher/auth/cookies.json`（`0600`），格式沿用 `{cookies:{}, headers:{Authorization}}`；日志仅记录名/过期/哈希，绝不记值。
- `scripts/fetch-latest.sh` 改动：jar 缺失/非法时调用 `nhk-easy auth capture` 代替原来“手工导出+回车”提示；其余 ffmpeg/安装/抓取逻辑不变。

## 2. 数据流（已确认）

```text
fetch-latest.sh -> jar 缺失?
  -> nhk-easy auth capture
     -> 启动本机 Chrome（临时 profile）打开 https://news.web.nhk/news/easy/
     -> 用户点完同意，回终端按 Enter（或 --timeout 超时）
     -> 读 web.nhk 后缀域 cookies（含 HttpOnly；实测同意后置在 `.web.nhk` 父域，2026-09-05 live 探针验证）+ cookie/localStorage 的 z_at
     -> 写 cookies.json + chmod 600
  -> nhk-easy fetch-latest --auth cookie_jar --audio m4a
```

## 3. 失败处理（已确认）

| 情形 | 行为 |
| --- | --- |
| 未装 Chrome / 未装 playwright | exit 2，打印安装指引，不动 profile |
| 用户关窗 / 超时 / 未点同意导致空会话 | exit 3，提示重跑，不写空文件覆盖旧 jar |
| jar 目录不可写 | exit 1，保留诊断，不重试 |
| 捕获到 cookie 但缺 `z_at` | 仍落盘 cookies，headers 为空，音频后续按现有 `audio_unavailable` 处理，文本先行 |

## 4. 测试

- 离线单测（默认）：用替身 Playwright 对象验证域过滤、空会话拒绝、`0600` 权限、值脱敏；`fetch-latest.sh` 增测 capture 分支调用不断真浏览器。
- 手动 live：一次真实点同意+落盘+`fetch-latest`，只记结构摘要（状态码/选择器/文件存在），不存内容进仓库。
- 门禁：`pytest -q`、`ruff check .`、`mypy src`、`bash -n`。

## 5. 合规边界

- 仅在用户可见浏览器中由用户本人点同意；不存密码、不碰主 profile、不伪造地域/身份/同意状态。
- 连续失败即停，不暴力刷新 token；Cookie 只存受限本地文件。
