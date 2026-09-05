# Auth Capture（自动保存 Cookie）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 缺 cookie 时弹本机已装 Chrome 供用户点一次同意，回车即自动落盘 `cookies.json`，`fetch-latest.sh` 全程一键。

**Architecture:** 新增 `auth_capture.py` 纯函数层（域过滤/payload 组装/0600 落盘，可离线单测）+ 可注入的 `run_capture` 编排（浏览器上下文与等待回车均可替身）；CLI 新增 `auth capture` 子命令，playwright 只做 lazy import 的可选依赖，`channel="chrome"` 复用本机 Chrome + 临时 profile（退出即删）。

**Tech Stack:** Python 3.11, Typer 0.25, Playwright（可选，`channel="chrome"`，不下载 Chromium）, pytest, ruff, mypy (strict).

**Spec:** `docs/superpowers/specs/2026-09-05-auth-capture-design.md`

## Global Constraints

- 绝不读取/写入日常 Chrome profile，不处理任何密码，不伪造同意/地域/身份。
- 临时 `user-data-dir` 退出时一律删除；jar 文件 `0600`，父目录 `0700`。
- 日志/终端只出现 cookie 名/过期/哈希，绝不出现值；HLS 带签 URL 不打印。
- `channel="chrome"`，禁止下载 Chromium（不跑 `playwright install`）。
- 退出码：成功 0；空会话/超时/用户取消 3；缺 Chrome/playwright 2。
- Consent URL 默认 `https://news.web.nhk/news/easy/`；cookie 只收 `web.nhk` 后缀域（实测同意后置在 `.web.nhk` 父域）。
- TDD：先失败测试，后最小实现；`pytest -q`、`ruff check .`、`mypy src`、`bash -n` 全绿。

---

## File map

- Create: `src/nhk_easy_fetcher/auth_capture.py` — 纯函数 + 编排（唯一懂 Playwright 的模块）。
- Modify: `src/nhk_easy_fetcher/errors.py` — 加 `BrowserUnavailable`。
- Modify: `src/nhk_easy_fetcher/cli.py` — 加 `auth` 子组 + `capture` 命令。
- Modify: `pyproject.toml` — 加 `browser` 可选依赖。
- Modify: `scripts/fetch-latest.sh` — jar 缺失/非法时调 `nhk-easy auth capture`。
- Test: `tests/test_auth_capture.py`（新建）, `tests/test_fetch_script.py`（扩展）。
- Docs: `README.md` one-click 小节更新。

---

### Task 1: 纯函数层 + `BrowserUnavailable`

**Files:**
- Modify: `src/nhk_easy_fetcher/errors.py`
- Create: `src/nhk_easy_fetcher/auth_capture.py`（仅纯函数部分）
- Test: `tests/test_auth_capture.py`

**Interfaces:**
- Consumes: `errors.AuthorizationUnavailable`, `errors.InsecureCredentialStore`（已存在）。
- Produces（Task 2/3 依赖，签名冻结）:
  - `filter_nhk_cookies(cookies: list[dict[str, Any]], domain_suffix: str = "news.web.nhk") -> dict[str, str]`
  - `extract_z_at(storage_state: dict[str, Any]) -> str | None`
  - `build_cookie_jar_payload(cookies: dict[str, str], z_at: str | None) -> dict[str, Any]`
  - `write_cookie_jar(path: Path, payload: dict[str, Any]) -> Path`
  - `CONSENT_URL: str = "https://news.web.nhk/news/easy/"`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

from nhk_easy_fetcher.auth_capture import (
    build_cookie_jar_payload,
    extract_z_at,
    filter_nhk_cookies,
)


def test_filter_keeps_only_nhk_domain_cookies() -> None:
    cookies = [
        {"name": "a", "value": "1", "domain": ".news.web.nhk"},
        {"name": "b", "value": "2", "domain": "news.web.nhk"},
        {"name": "tracker", "value": "x", "domain": ".example.com"},
        {"name": "empty", "value": "", "domain": ".news.web.nhk"},
    ]
    assert filter_nhk_cookies(cookies) == {"a": "1", "b": "2"}


def test_extract_z_at_from_storage_state() -> None:
    state = {
        "origins": [
            {
                "origin": "https://news.web.nhk",
                "localStorage": [{"name": "z_at", "value": "tok123"}],
            }
        ]
    }
    assert extract_z_at(state) == "tok123"


def test_extract_z_at_missing_returns_none() -> None:
    assert extract_z_at({"origins": []}) is None
    assert extract_z_at({}) is None


def test_build_payload_with_and_without_z_at() -> None:
    assert build_cookie_jar_payload({"a": "1"}, "tok") == {
        "cookies": {"a": "1"},
        "headers": {"Authorization": "Bearer tok"},
    }
    assert build_cookie_jar_payload({"a": "1"}, None) == {
        "cookies": {"a": "1"},
        "headers": {},
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth_capture.py -v`
Expected: FAIL with "No module named 'nhk_easy_fetcher.auth_capture'"（或 function not defined）。

- [ ] **Step 3: Write minimal implementation**

`src/nhk_easy_fetcher/errors.py` 追加：

```python
class BrowserUnavailable(NhkEasyFetcherError):
    """Local browser automation unavailable (missing browser or driver lib)."""
```

`src/nhk_easy_fetcher/auth_capture.py`：

```python
"""Capture NHK ONE consent cookies from the user's own installed Chrome.

Only entry that knows about Playwright. Everything else talks to the
cookie-jar file via CookieJarProvider.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

CONSENT_URL = "https://news.web.nhk/news/easy/"
NHK_DOMAIN_SUFFIX = "news.web.nhk"


def filter_nhk_cookies(
    cookies: list[dict[str, Any]], domain_suffix: str = NHK_DOMAIN_SUFFIX
) -> dict[str, str]:
    """Keep non-empty cookies whose domain is the NHK suffix (or its subdomains)."""
    kept: dict[str, str] = {}
    for cookie in cookies:
        domain = str(cookie.get("domain", ""))
        name = str(cookie.get("name", ""))
        value = str(cookie.get("value", ""))
        if not name or not value:
            continue
        if domain == domain_suffix or domain.endswith("." + domain_suffix):
            kept[name] = value
    return kept


def extract_z_at(storage_state: dict[str, Any]) -> str | None:
    """Return localStorage z_at for the mediatoken Authorization header, if present."""
    origins = storage_state.get("origins")
    if not isinstance(origins, list):
        return None
    for origin in origins:
        if not isinstance(origin, dict):
            continue
        entries = origin.get("localStorage")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("name") == "z_at":
                value = str(entry.get("value", ""))
                return value or None
    return None


def build_cookie_jar_payload(
    cookies: dict[str, str], z_at: str | None
) -> dict[str, Any]:
    """Build the CookieJarProvider-compatible payload (values stay in memory only)."""
    headers: dict[str, str] = {}
    if z_at:
        headers["Authorization"] = f"Bearer {z_at}"
    return {"cookies": dict(cookies), "headers": headers}


def write_cookie_jar(path: Path, payload: dict[str, Any]) -> Path:
    """Atomically write the jar with 0700 parent dir and 0600 file perms."""
    import json

    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    return target
```

- [ ] **Step 4: Write the write_cookie_jar failing test, watch fail, then pass**

```python
def test_write_cookie_jar_enforces_permissions(tmp_path: Path) -> None:
    import json
    import os

    from nhk_easy_fetcher.auth_capture import write_cookie_jar

    jar = tmp_path / "sub" / "cookies.json"
    out = write_cookie_jar(jar, {"cookies": {"a": "1"}, "headers": {}})
    assert out == jar.expanduser()
    assert json.loads(jar.read_text(encoding="utf-8"))["cookies"] == {"a": "1"}
    assert oct(os.stat(jar).st_mode & 0o777) == "0o600"
```

先跑确认失败（`write_cookie_jar` 未定义），实现已在 Step 3 给出，跑过即可。

- [ ] **Step 5: Run tests, ruff, mypy for touched files**

Run: `pytest tests/test_auth_capture.py -q`
Expected: PASS（7 tests）。

Run: `ruff check src/nhk_easy_fetcher/auth_capture.py src/nhk_easy_fetcher/errors.py tests/test_auth_capture.py`
Expected: All checks passed.

Run: `mypy src`
Expected: Success, no issues.

---

### Task 2: `run_capture` 编排 + `auth capture` CLI

**Files:**
- Modify: `src/nhk_easy_fetcher/auth_capture.py`（追加编排层）
- Modify: `src/nhk_easy_fetcher/cli.py`（加 `auth` 子组）
- Test: `tests/test_auth_capture.py`（追加）, `tests/test_cli.py`（追加，可新建 `tests/test_auth_cli.py` 更干净——选新建，避免改大文件）

**Interfaces:**
- Consumes（Task 1 冻结签名）: `filter_nhk_cookies`, `extract_z_at`, `build_cookie_jar_payload`, `write_cookie_jar`, `CONSENT_URL`；`errors.AuthorizationUnavailable`, `errors.BrowserUnavailable`。
- Produces（Task 3 依赖）:
  - `run_capture(cookie_jar: Path, *, consent_url: str = CONSENT_URL, timeout_seconds: float = 300, open_context: Callable[[], AbstractContextManager[CaptureContext]] | None = None, wait_for_user: Callable[[float], bool] | None = None) -> Path`
  - `CaptureContext` Protocol：`cookies() -> list[dict[str, Any]]`，`storage_state() -> dict[str, Any]`。
  - CLI：`nhk-easy auth capture [--cookie-jar PATH] [--timeout SEC]`，退出码 0/2/3。

- [ ] **Step 1: Write the failing orchestration tests**

```python
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest

from nhk_easy_fetcher import auth_capture
from nhk_easy_fetcher.errors import AuthorizationUnavailable


class FakeContext:
    def __init__(self, cookies: list[dict[str, Any]], state: dict[str, Any]) -> None:
        self._cookies = cookies
        self._state = state

    def cookies(self) -> list[dict[str, Any]]:
        return self._cookies

    def storage_state(self) -> dict[str, Any]:
        return self._state


def _ctx(cookies: list[dict[str, Any]], state: dict[str, Any]) -> Any:
    @contextmanager
    def _open() -> Iterator[FakeContext]:
        yield FakeContext(cookies, state)

    return _open


def test_run_capture_writes_jar(tmp_path: Path) -> None:
    import json

    jar = tmp_path / "cookies.json"
    out = auth_capture.run_capture(
        jar,
        open_context=_ctx(
            [{"name": "a", "value": "1", "domain": ".news.web.nhk"}],
            {"origins": [{"origin": "https://news.web.nhk", "localStorage": [{"name": "z_at", "value": "t"}]}]},
        ),
        wait_for_user=lambda timeout: True,
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["cookies"] == {"a": "1"}
    assert payload["headers"] == {"Authorization": "Bearer t"}


def test_run_capture_empty_session_raises(tmp_path: Path) -> None:
    with pytest.raises(AuthorizationUnavailable):
        auth_capture.run_capture(
            tmp_path / "cookies.json",
            open_context=_ctx([], {"origins": []}),
            wait_for_user=lambda timeout: True,
        )


def test_run_capture_timeout_raises(tmp_path: Path) -> None:
    with pytest.raises(AuthorizationUnavailable):
        auth_capture.run_capture(
            tmp_path / "cookies.json",
            open_context=_ctx([], {"origins": []}),
            wait_for_user=lambda timeout: False,
        )
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_auth_capture.py -v`
Expected: FAIL with "module has no attribute 'run_capture'"。

- [ ] **Step 3: Minimal orchestration implementation**（追加到 `auth_capture.py`）

```python
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol


class CaptureContext(Protocol):
    def cookies(self) -> list[dict[str, Any]]: ...
    def storage_state(self) -> dict[str, Any]: ...


def wait_for_enter(timeout_seconds: float) -> bool:
    """Wait for Enter on stdin; True if pressed, False on timeout."""
    import select
    import sys

    sys.stderr.write("In the opened Chrome, complete consent, then press Enter here...\n")
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
    except (OSError, ValueError, AttributeError):
        sys.stdin.readline()
        return True
    if not ready:
        return False
    sys.stdin.readline()
    return True


@contextmanager
def open_chrome_context(
    user_data_dir: str, consent_url: str = CONSENT_URL
) -> Iterator[Any]:
    """Launch installed Chrome via Playwright (channel='chrome'). Lazy import."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        from nhk_easy_fetcher.errors import BrowserUnavailable

        raise BrowserUnavailable(
            "Playwright is not installed. Run: pip install -e '.[browser]'"
        ) from exc
    from nhk_easy_fetcher.errors import BrowserUnavailable

    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir,
                channel="chrome",
                headless=False,
                args=["--no-first-run", "--no-default-browser-check"],
            )
        except Exception as exc:
            raise BrowserUnavailable(
                "Could not launch installed Google Chrome. "
                "Install Chrome and retry."
            ) from exc
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(consent_url)
        try:
            yield context
        finally:
            context.close()


def run_capture(
    cookie_jar: Path,
    *,
    consent_url: str = CONSENT_URL,
    timeout_seconds: float = 300,
    open_context: Callable[[], AbstractContextManager[Any]] | None = None,
    wait_for_user: Callable[[float], bool] | None = None,
) -> Path:
    """Open Chrome, let the user consent, then auto-save the jar. Returns jar path."""
    import shutil
    import tempfile

    from nhk_easy_fetcher.errors import AuthorizationUnavailable

    wait = wait_for_user or wait_for_enter
    user_data_dir = tempfile.mkdtemp(prefix="nhk-easy-profile-")
    os.chmod(user_data_dir, 0o700)
    try:
        opener = open_context or (lambda: open_chrome_context(user_data_dir, consent_url))
        with opener() as context:
            if not wait(timeout_seconds):
                raise AuthorizationUnavailable(
                    "Timed out waiting for consent. Re-run and press Enter after consent."
                )
            raw_cookies = context.cookies()
            try:
                state = context.storage_state()
            except Exception:
                state = {}
            cookies = filter_nhk_cookies(raw_cookies)
            if not cookies:
                raise AuthorizationUnavailable(
                    "No news.web.nhk cookies captured. Complete consent and retry."
                )
            payload = build_cookie_jar_payload(cookies, extract_z_at(state))
            return write_cookie_jar(cookie_jar, payload)
    finally:
        shutil.rmtree(user_data_dir, ignore_errors=True)
```

- [ ] **Step 4: Run orchestration tests**

Run: `pytest tests/test_auth_capture.py -q`
Expected: PASS（10 tests）。

- [ ] **Step 5: Write failing CLI tests**（新建 `tests/test_auth_cli.py`）

```python
from typer.testing import CliRunner

from nhk_easy_fetcher.cli import app

runner = CliRunner()


def test_auth_capture_success(tmp_path, monkeypatch) -> None:
    import nhk_easy_fetcher.cli as cli_mod

    jar = tmp_path / "cookies.json"
    monkeypatch.setattr(cli_mod, "auth_capture", FakeCapture(jar))
    result = runner.invoke(app, ["auth", "capture", "--cookie-jar", str(jar)])
    assert result.exit_code == 0


def test_auth_capture_empty_exits_3(tmp_path, monkeypatch) -> None:
    import nhk_easy_fetcher.cli as cli_mod
    from nhk_easy_fetcher.errors import AuthorizationUnavailable

    class Failing:
        def run_capture(self, *a: object, **k: object) -> object:
            raise AuthorizationUnavailable("empty")

    monkeypatch.setattr(cli_mod, "auth_capture", Failing())
    result = runner.invoke(app, ["auth", "capture", "--cookie-jar", str(tmp_path / "c.json")])
    assert result.exit_code == 3


def test_auth_capture_no_browser_exits_2(tmp_path, monkeypatch) -> None:
    import nhk_easy_fetcher.cli as cli_mod
    from nhk_easy_fetcher.errors import BrowserUnavailable

    class NoBrowser:
        def run_capture(self, *a: object, **k: object) -> object:
            raise BrowserUnavailable("no chrome")

    monkeypatch.setattr(cli_mod, "auth_capture", NoBrowser())
    result = runner.invoke(app, ["auth", "capture", "--cookie-jar", str(tmp_path / "c.json")])
    assert result.exit_code == 2
```

其中 `FakeCapture` 为测试内小类：`class FakeCapture: def __init__(self, jar): self._jar = jar; def run_capture(self, *a, **k): self._jar.write_text("{}"); return self._jar`（写全，不要省略）。

- [ ] **Step 6: Run to verify fail**

Run: `pytest tests/test_auth_cli.py -v`
Expected: FAIL（`auth` 无此命令，exit code 非预期）。

- [ ] **Step 7: Minimal CLI wiring**（`cli.py` 追加，`from nhk_easy_fetcher import auth_capture` 放函数内 lazy import 保持启动轻量）

```python
auth_app = typer.Typer(no_args_is_help=True, help="Browser consent cookie capture.")
app.add_typer(auth_app, name="auth")


@auth_app.command("capture")
def auth_capture_cmd(
    cookie_jar: Annotated[Path | None, typer.Option("--cookie-jar")] = None,
    timeout: Annotated[float, typer.Option("--timeout")] = 300,
    consent_url: Annotated[str, typer.Option("--consent-url")] = "https://news.web.nhk/news/easy/",
) -> None:
    """Open installed Chrome for consent, then auto-save the cookie jar."""
    from nhk_easy_fetcher import auth_capture
    from nhk_easy_fetcher.config import load_config
    from nhk_easy_fetcher.errors import AuthorizationUnavailable, BrowserUnavailable

    config = load_config()
    jar = (cookie_jar or config.auth.cookie_jar_path).expanduser().resolve()
    try:
        saved = auth_capture.run_capture(jar, consent_url=consent_url, timeout_seconds=timeout)
    except BrowserUnavailable as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None
    except AuthorizationUnavailable as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(3) from None
    console.print(f"Saved cookie jar to {saved}")
    raise typer.Exit(0)
```

注意：`cli.py` 顶部已有 `from pathlib import Path`，确认存在；`Annotated` 已导入。

- [ ] **Step 8: Run CLI tests + gates**

Run: `pytest tests/test_auth_cli.py tests/test_auth_capture.py -q`
Expected: PASS。

Run: `ruff check src tests && mypy src`
Expected: clean。

---

### Task 3: 脚本联调 + `browser` extra + 文档

**Files:**
- Modify: `pyproject.toml`, `scripts/fetch-latest.sh`, `README.md`, `tests/test_fetch_script.py`

**Interfaces:**
- Consumes: `nhk-easy auth capture --cookie-jar PATH`（Task 2）。
- Produces: 一键脚本缺 jar 即自动 capture 后继续抓取。

- [ ] **Step 1: Extend the failing script tests**

在 `tests/test_fetch_script.py` 追加：

```python
def test_fetch_latest_script_uses_auth_capture() -> None:
    from pathlib import Path

    text = (Path(__file__).parents[1] / "scripts" / "fetch-latest.sh").read_text(encoding="utf-8")
    assert "auth capture" in text
    assert "nhk-easy auth capture --cookie-jar" in text
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_fetch_script.py -v`
Expected: FAIL（`auth capture` 不在脚本中）。

- [ ] **Step 3: Minimal changes**

`pyproject.toml` 的 `[project.optional-dependencies]` 加一行：

```toml
browser = [
    "playwright>=1.40",
]
```

`scripts/fetch-latest.sh` 把 `ensure_cookie_jar` 中“缺文件即 open_url + read 等待”整段替换为：

```bash
ensure_cookie_jar() {
  if [[ ! -f "$COOKIE_JAR" ]]; then
    mkdir -p "$(dirname "$COOKIE_JAR")"
    echo "Cookie jar not found, opening installed Chrome for one-time consent..." >&2
    if ! nhk-easy auth capture --cookie-jar "$COOKIE_JAR"; then
      echo "Cookie capture did not complete (see message above)." >&2
      exit 3
    fi
  fi

  if [[ ! -f "$COOKIE_JAR" ]]; then
    echo "Cookie jar still missing at $COOKIE_JAR" >&2
    exit 3
  fi

  chmod 600 "$COOKIE_JAR"

  if ! COOKIE_JAR="$COOKIE_JAR" python3 -c "import json, os; d=json.load(open(os.path.expanduser(os.environ['COOKIE_JAR']))); c=d.get('cookies') if isinstance(d.get('cookies'), dict) else {}; h=d.get('headers') if isinstance(d.get('headers'), dict) else {}; flat={k: v for k, v in d.items() if k not in ('cookies', 'headers') and not k.startswith('_')}; assert isinstance(d, dict) and (bool(c) or bool(h) or bool(flat))"; then
    echo "Cookie jar at $COOKIE_JAR looks empty or invalid. Removing it and capturing again..." >&2
    rm -f "$COOKIE_JAR"
    nhk-easy auth capture --cookie-jar "$COOKIE_JAR" || exit 3
    chmod 600 "$COOKIE_JAR"
  fi
}
```

注意：`open_url` 函数保留（capture 失败回退提示用）或删除——选删除，并在测试中不许出现 `read -r _` 手工等待。同步更新 `test_fetch_latest_script_covers_one_click_flow`：把 `"news.web.nhk/news/easy"` 断言保留（脚本注释/变量仍可含），删去对 `Press Enter` 的依赖（现有测试无此断言，无须改）。

`README.md` one-click 小节追加：

```markdown
首次运行会自动弹起本机 Chrome 供你点一次同意，回车即自动保存 cookie（需 `pip install -e '.[browser]'`，不下载新浏览器，不碰日常 profile）。
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_fetch_script.py -q`
Expected: PASS（4 tests）。

Run: `bash -n scripts/fetch-latest.sh && echo OK`
Expected: OK。

---

### Task 4: 全部门禁 + 手动 live 清单

- [ ] **Step 1: Run full gates**

Run: `python -m pytest 2>&1 | tail -2`
Expected: 全部通过（基线 74 + 新增约 14）。

Run: `ruff check . && mypy src`
Expected: All checks passed / Success: no issues found。

- [ ] **Step 2: Offline behavior dry-run（无真浏览器）**

Run: `AUDIO_MODE=wav ./scripts/fetch-latest.sh; echo "EXIT:$?"`
Expected: `Unknown AUDIO_MODE`，exit 2。

Run: `echo '{"cookies":{}}' > /tmp/empty-cookies.json; COOKIE_JAR=/tmp/empty-cookies.json AUDIO_MODE=off ./scripts/fetch-latest.sh; echo "EXIT:$?"`
Expected: capture 分支被调用（无 playwright 时 exit 2 提示安装，或有 Chrome 时弹浏览器——CI/无头机以前者为准，不可卡住等待输入）。

- [ ] **Step 3: Manual live checklist（人工在有屏 Mac 上做，不进仓库）**

1. `pip install -e '.[browser]'`
2. `rm -f ~/.nhk-easy-fetcher/auth/cookies.json; ./scripts/fetch-latest.sh` → 弹 Chrome → 点同意 → 回车 → `ls -l` 确认 `cookies.json` 为 `-rw-------`，`article.txt` 与 `audio.m4a` 落盘。
3. 记录结构摘要到 `docs/verification/`（状态码/选择器/文件存在与否，不贴内容）。

---

## Self-Review

- Spec 覆盖：临时 profile 退出即删（Task 2 `finally`）✓；0600/0700（Task 1 `write_cookie_jar`）✓；只记名不记值（全程无打印 payload；CLI 只打印路径）✓；`channel="chrome"` 不下载 ✓；退出码 0/2/3 ✓；`z_at` 缺失文本先行（spec §3，`headers={}` 回退，fetch 侧现有 `audio_unavailable` 承接）✓；默认超时 300 ✓。
- 占位检查：无 TBD/TODO/“类似 Task N”省略，所有测试与实现代码均全文给出。
- 类型一致：`run_capture(...) -> Path` 在 Task 2 定义、Task 3 脚本只调 CLI 不直调、`CaptureContext` 两方法签名在测试替身与实现一致。
