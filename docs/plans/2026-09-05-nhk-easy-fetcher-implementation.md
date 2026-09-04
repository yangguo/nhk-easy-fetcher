# nhk-easy-fetcher Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a local-first Python CLI that safely discovers NHK NEWS WEB EASY articles, exports complete article text, and optionally saves legitimately available HLS audio for personal study.

**Architecture:** A Typer CLI invokes a small application service. Discovery, authorization, HTTP, page-mode detection, parsing, audio, storage, and scheduling are separate adapters around a normalized `ArticleRecord`; SQLite is the sole persistent state. The source site is treated as a volatile contract, so full-text acquisition fails closed when classic EASY HTML is unavailable.

**Tech Stack:** Python 3.11+, `httpx`, `pydantic`, `Typer`, `selectolax`, standard-library `sqlite3`, `ffmpeg`/`ffprobe`, `pytest`, `respx`, `ruff`, and `mypy`.

**Non-negotiable constraints:** No fetched NHK content/cookies in Git; no hosted API; no access-control, region, consent, login, DRM, or rate-limit bypass; production full-text requests require an authorized, browser-compatible session that is legally usable by the user.

## Preconditions

- Read [the development specification](../development.md) and [reference notes](../references.md) first.
- Work on a clean branch; do not commit local output, SQLite, cookies, captured response bodies, or audio.
- Use `@superpowers:test-driven-development` for every behavior change and `@superpowers:verification-before-completion` before every feature/Release claim.
- Keep default tests offline. A live source-contract probe is manual/opt-in only and must not persist article content.

## Task 1: Create the Python package and quality gates

**Files:**

- Create: `pyproject.toml`
- Create: `src/nhk_easy_fetcher/__init__.py`
- Create: `src/nhk_easy_fetcher/py.typed`
- Create: `tests/test_package.py`
- Create: `.github/workflows/ci.yml`

**Step 1: Write the failing package test**

```python
# tests/test_package.py
from nhk_easy_fetcher import __version__


def test_exposes_a_semantic_version() -> None:
    assert __version__ == "0.1.0"
```

**Step 2: Run it to verify the failure**

Run: `python -m pytest tests/test_package.py -q`

Expected: collection failure because the package does not exist.

**Step 3: Add the minimum package metadata and implementation**

```toml
# pyproject.toml (key parts)
[project]
name = "nhk-easy-fetcher"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["httpx>=0.27", "pydantic>=2.7", "typer>=0.12", "selectolax>=0.3"]

[project.scripts]
nhk-easy = "nhk_easy_fetcher.cli:app"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

```python
# src/nhk_easy_fetcher/__init__.py
__version__ = "0.1.0"
```

The CI workflow must run `python -m pip install -e '.[dev]'`, `ruff check .`, `mypy src`, and `pytest -q` on supported Python versions. Do not add a scheduled fetch job to CI.

**Step 4: Run the focused test and quality tools**

Run: `python -m pytest tests/test_package.py -q && ruff check . && mypy src`

Expected: all commands pass.

**Step 5: Commit**

```bash
git add pyproject.toml src/ tests/test_package.py .github/workflows/ci.yml
git commit -m "build: bootstrap Python package and CI"
```

## Task 2: Define immutable domain models and configuration

**Files:**

- Create: `src/nhk_easy_fetcher/models.py`
- Create: `src/nhk_easy_fetcher/config.py`
- Create: `tests/test_models.py`
- Create: `tests/test_config.py`

**Step 1: Write failing tests for the two page modes and safe defaults**

```python
from nhk_easy_fetcher.config import FetchConfig
from nhk_easy_fetcher.models import ContentStatus, PageMode


def test_default_config_is_low_rate_and_disallows_partial() -> None:
    config = FetchConfig()
    assert config.max_concurrency == 1
    assert config.min_interval_seconds >= 1.0
    assert config.allow_partial is False


def test_complete_status_requires_classic_page_mode() -> None:
    assert ContentStatus.for_page_mode(PageMode.CLASSIC_COMPLETE).value == "complete"
```

**Step 2: Run it to verify the failure**

Run: `python -m pytest tests/test_models.py tests/test_config.py -q`

Expected: import failure.

**Step 3: Implement the minimum typed model surface**

```python
class PageMode(StrEnum):
    CLASSIC_COMPLETE = "classic_complete"
    NEXT_PARTIAL = "next_partial"
    UNKNOWN = "unknown"


class ContentStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"

    @classmethod
    def for_page_mode(cls, page_mode: PageMode) -> "ContentStatus":
        return cls.COMPLETE if page_mode is PageMode.CLASSIC_COMPLETE else cls.UNAVAILABLE


class FetchConfig(BaseModel):
    timezone: str = "Asia/Tokyo"
    request_timeout_seconds: float = 20
    max_retries: int = 2
    min_interval_seconds: float = 1.5
    max_concurrency: int = 1
    allow_partial: bool = False
```

Use Pydantic validation to reject `max_concurrency > 1` in v0 and negative timeouts. Add `load_config(path: Path | None) -> AppConfig`; it may read TOML but must never parse raw Cookie values from environment variables.

**Step 4: Run targeted tests**

Run: `python -m pytest tests/test_models.py tests/test_config.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/nhk_easy_fetcher/models.py src/nhk_easy_fetcher/config.py tests/
git commit -m "feat: add typed fetch configuration and source models"
```

## Task 3: Implement a fail-closed page-mode detector and parser fixtures

**Files:**

- Create: `src/nhk_easy_fetcher/page_mode.py`
- Create: `tests/fixtures/classic_complete.html`
- Create: `tests/fixtures/next_partial.html`
- Create: `tests/fixtures/unknown.html`
- Create: `tests/test_page_mode.py`

**Step 1: Add synthetic failing detection tests**

```python
from nhk_easy_fetcher.models import PageMode
from nhk_easy_fetcher.page_mode import detect_page_mode


def test_detects_classic_page_only_when_full_body_selector_exists(load_fixture):
    html = load_fixture("classic_complete.html")
    assert detect_page_mode(html) is PageMode.CLASSIC_COMPLETE


def test_does_not_mistake_nhk_one_shell_for_complete_article(load_fixture):
    html = load_fixture("next_partial.html")
    assert detect_page_mode(html) is PageMode.NEXT_PARTIAL
```

**Step 2: Run the tests to verify failure**

Run: `python -m pytest tests/test_page_mode.py -q`

Expected: import failure.

**Step 3: Implement a strict detector**

```python
def detect_page_mode(html: str) -> PageMode:
    tree = HTMLParser(html)
    if tree.css_first("#js-article-body") is not None:
        return PageMode.CLASSIC_COMPLETE
    if "NHK ONE" in tree.text() or tree.css_first("script#__NEXT_DATA__") is not None:
        return PageMode.NEXT_PARTIAL
    return PageMode.UNKNOWN
```

The classic fixture must be hand-written with title/date/body/ruby structure. The partial fixture must contain only a tiny generic NHK ONE/Next shell, never a copied real page. The unknown fixture must prove that HTTP success plus a generic `<h1>` is insufficient.

**Step 4: Run focused tests**

Run: `python -m pytest tests/test_page_mode.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/nhk_easy_fetcher/page_mode.py tests/fixtures tests/test_page_mode.py
git commit -m "feat: detect complete and partial EASY page modes"
```

## Task 4: Add sitemap discovery with URL and date filtering

**Files:**

- Create: `src/nhk_easy_fetcher/discovery.py`
- Create: `tests/fixtures/easy_sitemap.xml`
- Create: `tests/test_discovery.py`

**Step 1: Write failing tests before the parser**

```python
from datetime import date

from nhk_easy_fetcher.discovery import discover_article_urls


def test_keeps_only_easily_identified_article_urls(load_fixture):
    articles = discover_article_urls(load_fixture("easy_sitemap.xml"))
    assert [article.article_id for article in articles] == ["ne2026090512345"]


def test_filters_to_requested_japan_business_date(load_fixture):
    articles = discover_article_urls(load_fixture("easy_sitemap.xml"), only_date=date(2026, 9, 5))
    assert len(articles) == 1
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_discovery.py -q`

Expected: import failure.

**Step 3: Implement parser and validation**

```python
ARTICLE_RE = re.compile(r"/easy/(ne\d{13})/\1\.html$")


def discover_article_urls(sitemap_xml: str, only_date: date | None = None) -> list[DiscoveredArticle]:
    root = ElementTree.fromstring(sitemap_xml)
    urls = []
    for loc in root.findall(".//{*}loc"):
        match = ARTICLE_RE.search(loc.text or "")
        if match:
            urls.append(DiscoveredArticle(article_id=match.group(1), source_url=loc.text))
    return dedupe_and_filter(urls, only_date)
```

Derive the article date only from a documented article ID/date rule that has unit coverage; if uncertain, filter by page-published date after fetching instead. Non-article pages such as disaster explainers must never enter the fetch queue.

**Step 4: Run tests and lint**

Run: `python -m pytest tests/test_discovery.py -q && ruff check src/nhk_easy_fetcher/discovery.py`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/nhk_easy_fetcher/discovery.py tests/fixtures/easy_sitemap.xml tests/test_discovery.py
git commit -m "feat: discover article URLs from EASY sitemap"
```

## Task 5: Parse complete article HTML into dual ruby views

**Files:**

- Create: `src/nhk_easy_fetcher/parser.py`
- Modify: `src/nhk_easy_fetcher/models.py`
- Create: `tests/test_parser.py`

**Step 1: Write exact failing expectations**

```python
from nhk_easy_fetcher.parser import parse_complete_article


def test_parser_keeps_ruby_and_builds_plain_and_reading_views(load_fixture):
    article = parse_complete_article(load_fixture("classic_complete.html"), source_url="https://example.test/ne...")
    assert article.title.plain == "漢字のニュース"
    assert article.paragraphs[0].plain == "漢字を読む。"
    assert article.paragraphs[0].with_readings == "漢字（かんじ）を読む。"


def test_parser_rejects_partial_page(load_fixture):
    with pytest.raises(FullContentUnavailable):
        parse_complete_article(load_fixture("next_partial.html"), source_url="https://example.test/ne...")
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_parser.py -q`

Expected: import failure.

**Step 3: Implement the smallest safe parser**

```python
def parse_complete_article(html: str, source_url: str) -> ArticleRecord:
    if detect_page_mode(html) is not PageMode.CLASSIC_COMPLETE:
        raise FullContentUnavailable(source_url)
    tree = HTMLParser(html)
    body = required(tree.css_first("#js-article-body"), "article body")
    paragraphs = [parse_paragraph(node) for node in body.css("p") if node.text(strip=True)]
    return ArticleRecord(title=parse_title(tree), published_at=parse_date(tree), paragraphs=paragraphs, ...)
```

Implement ruby transformation by walking nodes: discard `rt`/`rp` for plain, append `（reading）` for readings, preserve a sanitised HTML version. Remove scripts, styles, event attributes, and external `href` values. Do not parse the full document with regex.

**Step 4: Run tests**

Run: `python -m pytest tests/test_parser.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/nhk_easy_fetcher/parser.py src/nhk_easy_fetcher/models.py tests/test_parser.py
git commit -m "feat: parse complete EASY articles with ruby views"
```

## Task 6: Implement HTTP client, low-rate retry policy, and error taxonomy

**Files:**

- Create: `src/nhk_easy_fetcher/errors.py`
- Create: `src/nhk_easy_fetcher/client.py`
- Create: `tests/test_client.py`

**Step 1: Write mocked failure tests**

```python
@respx.mock
def test_401_is_not_retried_as_a_network_error():
    route = respx.get("https://example.test/article").respond(401)
    with pytest.raises(AuthorizationUnavailable):
        client.get_text("https://example.test/article")
    assert route.call_count == 1


@respx.mock
def test_503_is_retried_only_up_to_configured_limit():
    route = respx.get("https://example.test/article").mock(side_effect=[httpx.Response(503), httpx.Response(200, text="ok")])
    assert client.get_text("https://example.test/article") == "ok"
    assert route.call_count == 2
```

**Step 2: Verify failure**

Run: `python -m pytest tests/test_client.py -q`

Expected: import failure.

**Step 3: Implement typed exceptions and one client policy**

```python
class SourceContractChanged(RuntimeError): ...
class AuthorizationUnavailable(RuntimeError): ...
class RateLimited(RuntimeError): ...
class NetworkTransient(RuntimeError): ...


RETRYABLE_STATUS_CODES = {502, 503, 504}
```

`HttpClient` must use explicit connect/read/write/pool timeouts, one request at a time, configurable minimum interval, bounded retry with jitter, and redacted logs. It must honor `Retry-After` up to a safe ceiling then stop. 401/403/429 are not normal exponential-retry cases.

**Step 4: Run tests**

Run: `python -m pytest tests/test_client.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/nhk_easy_fetcher/errors.py src/nhk_easy_fetcher/client.py tests/test_client.py
git commit -m "feat: add cautious HTTP policy and source errors"
```

## Task 7: Build SQLite state, atomic output, and exports

**Files:**

- Create: `src/nhk_easy_fetcher/storage.py`
- Create: `tests/test_storage.py`
- Create: `tests/test_exports.py`

**Step 1: Write failing idempotency and half-write tests**

```python
def test_complete_article_is_skipped_only_when_requested_artifacts_verify(tmp_path, article):
    store = StateStore(tmp_path)
    store.save_complete(article, formats={"json", "markdown", "text"})
    assert store.should_fetch(article.article_id, {"json", "markdown", "text"}) is False
    (store.article_dir(article.article_id) / "article.md").unlink()
    assert store.should_fetch(article.article_id, {"json", "markdown", "text"}) is True


def test_failed_atomic_write_never_marks_article_complete(tmp_path, article, monkeypatch):
    ...
    assert store.get_status(article.article_id) != "complete"
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_storage.py tests/test_exports.py -q`

Expected: import failure.

**Step 3: Implement single-transaction completion**

```python
with sqlite_transaction(connection):
    write_temp_artifacts(article)
    verify_hashes(temp_artifacts)
    promote_temp_artifacts_atomically()
    record_complete_article_and_artifacts(connection, article, artifacts)
```

Use `article_id` as the unique key. `article.json` has `schema_version`, source URL, time, parser mode, status, tool version, and SHA-256 values. `article.md` includes source/provenance and personal-study warning. `article.txt` uses plain text unless explicitly requested otherwise. Never write raw HTTP content by default.

**Step 4: Run tests**

Run: `python -m pytest tests/test_storage.py tests/test_exports.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/nhk_easy_fetcher/storage.py tests/test_storage.py tests/test_exports.py
git commit -m "feat: persist verified local article exports"
```

## Task 8: Add authorization abstraction without a bypass implementation

**Files:**

- Create: `src/nhk_easy_fetcher/auth.py`
- Create: `tests/test_auth.py`
- Modify: `src/nhk_easy_fetcher/config.py`

**Step 1: Write tests for safely scoped providers**

```python
def test_no_auth_provider_never_invents_credentials():
    assert NoAuthProvider().get_session(now).cookies == {}


def test_cookie_jar_provider_refuses_world_readable_file(tmp_path):
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text("{}")
    cookie_file.chmod(0o644)
    with pytest.raises(InsecureCredentialStore):
        CookieJarProvider(cookie_file).get_session(now)
```

**Step 2: Verify failure**

Run: `python -m pytest tests/test_auth.py -q`

Expected: import failure.

**Step 3: Implement only the contract and safe local providers**

```python
class AuthProvider(Protocol):
    def get_session(self, now: datetime) -> AuthorizedSession: ...
    def invalidate(self, reason: str) -> None: ...


class NoAuthProvider: ...
class CookieJarProvider: ...
class FixtureAuthProvider: ...
```

`CookieJarProvider` accepts a user-created, permission-restricted file path and redacts values in errors. Do **not** implement code that fabricates consent, identity, or geographic data. A future browser-compatible anonymous provider requires a separate design, a review of current terms, manual live validation, and a user-visible opt-in.

**Step 4: Run tests**

Run: `python -m pytest tests/test_auth.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/nhk_easy_fetcher/auth.py src/nhk_easy_fetcher/config.py tests/test_auth.py
git commit -m "feat: isolate authorization sessions behind safe providers"
```

## Task 9: Add metadata and HLS audio adapters

**Files:**

- Create: `src/nhk_easy_fetcher/audio.py`
- Create: `tests/test_audio.py`
- Create: `tests/fixtures/master.m3u8`

**Step 1: Write source-aware failing tests**

```python
def test_resolves_hls_from_voice_uri_not_article_id():
    hls = resolve_hls_url("voice-20260905.mp4")
    assert hls == "https://vod-stream.nhk.jp/news/easy_audio/voice-20260905/index.m3u8"


def test_refuses_html_error_page_as_manifest():
    with pytest.raises(AudioUnavailable):
        validate_manifest("<html>not found</html>", content_type="text/html")
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_audio.py -q`

Expected: import failure.

**Step 3: Implement resolver and testable subprocess wrapper**

```python
def resolve_hls_url(news_easy_voice_uri: str) -> str:
    stem = Path(news_easy_voice_uri).stem
    if not stem or stem == ".":
        raise AudioUnavailable("missing voice URI")
    return f"https://vod-stream.nhk.jp/news/easy_audio/{quote(stem)}/index.m3u8"
```

Inject a `CommandRunner` so tests never execute real `ffmpeg`. The production command writes to a `.partial` M4A, uses `-nostdin`, does not echo credentials, uses stream copy first, calls `ffprobe`, and only atomically promotes a valid file. Do not derive audio from `article_id`; do not handle DRM or protected manifests.

**Step 4: Run tests**

Run: `python -m pytest tests/test_audio.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/nhk_easy_fetcher/audio.py tests/test_audio.py tests/fixtures/master.m3u8
git commit -m "feat: resolve and validate optional HLS audio"
```

## Task 10: Orchestrate an idempotent fetch run

**Files:**

- Create: `src/nhk_easy_fetcher/app.py`
- Create: `tests/test_app.py`

**Step 1: Write an end-to-end offline test with fakes**

```python
def test_run_skips_known_complete_article_and_fetches_only_new_article(tmp_path, fake_discovery, fake_client):
    app = FetchApplication(...)
    first = app.fetch_today()
    second = app.fetch_today()
    assert first.completed == 1
    assert second.skipped == 1
    assert fake_client.article_requests == 1
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_app.py -q`

Expected: import failure.

**Step 3: Implement explicit run states**

```python
for candidate in discovery.discover(...):
    if store.should_fetch(candidate.article_id, formats):
        result = fetch_one(candidate)
        summary.add(result)
    else:
        summary.add_skipped(candidate.article_id)
```

`fetch_one` must detect page mode before parsing; `NEXT_PARTIAL` yields `authorization_required` unless the caller explicitly requested `allow_partial`, and still never yields `complete`. Audio failures yield a partial run without corrupting text exports. Record run summary JSON without article bodies.

**Step 4: Run focused tests**

Run: `python -m pytest tests/test_app.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/nhk_easy_fetcher/app.py tests/test_app.py
git commit -m "feat: orchestrate idempotent local fetch runs"
```

## Task 11: Expose CLI commands and stable exit codes

**Files:**

- Create: `src/nhk_easy_fetcher/cli.py`
- Create: `tests/test_cli.py`
- Modify: `README.md`

**Step 1: Write CLI runner tests**

```python
def test_dry_run_does_not_create_output(runner, tmp_path):
    result = runner.invoke(app, ["fetch", "--date", "2026-09-05", "--output", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0
    assert not (tmp_path / ".nhk-easy-fetcher").exists()


def test_authorization_failure_has_dedicated_exit_code(runner, monkeypatch):
    ...
    assert result.exit_code == 3
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_cli.py -q`

Expected: import failure.

**Step 3: Implement `fetch`, `status`, `verify`, `cleanup`, and `probe`**

Map domain errors precisely to 0–5 described in `docs/development.md`. `probe` defaults to fixture/offline behavior; `--live` requires both `NHK_EASY_LIVE_TEST=1` and a user-visible confirmation flag such as `--i-understand-live-requests`. CLI output must never print Cookie values, raw response bodies, or full HLS URLs with query strings.

**Step 4: Run CLI tests**

Run: `python -m pytest tests/test_cli.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/nhk_easy_fetcher/cli.py tests/test_cli.py README.md
git commit -m "feat: add safe fetcher command-line interface"
```

## Task 12: Add source-contract workflow and local scheduling documentation

**Files:**

- Create: `.github/workflows/source-contract.yml`
- Create: `docs/operations.md`
- Modify: `docs/development.md`
- Create: `tests/test_contract_summary.py`

**Step 1: Write a test for redacted contract summaries**

```python
def test_contract_summary_has_no_body_or_cookie_values():
    summary = ContractSummary(status_code=200, page_mode="classic_complete", body_hash="abc123")
    rendered = summary.to_json()
    assert "cookie" not in rendered.lower()
    assert "article body" not in rendered.lower()
```

**Step 2: Run to verify failure**

Run: `python -m pytest tests/test_contract_summary.py -q`

Expected: import failure.

**Step 3: Implement docs and workflow**

`source-contract.yml` must use `workflow_dispatch` by default. If a schedule is later enabled, it must be low-frequency, sitemap-only unless a repository owner manually enables live access, and must never upload/download content artifacts or commit results. `docs/operations.md` must give `launchd`, systemd timer, cron, and Task Scheduler examples that run the user-local output path and stop after repeated authorization/contract failures.

**Step 4: Run tests and validate YAML**

Run: `python -m pytest tests/test_contract_summary.py -q && ruff check . && python -c "import yaml; yaml.safe_load(open('.github/workflows/source-contract.yml'))"`

Expected: PASS. Add `PyYAML` to dev dependencies if used solely for this validation; otherwise use the platform workflow parser.

**Step 5: Commit**

```bash
git add .github/workflows/source-contract.yml docs/operations.md docs/development.md tests/test_contract_summary.py
git commit -m "docs: add safe source-contract and scheduling guidance"
```

## Task 13: Perform one manual, minimal live validation only after review

**Files:**

- Create: `docs/verification/2026-XX-XX-live-contract.md` (only a redacted structural report)
- Modify: `CHANGELOG.md`

**Step 1: Prepare an empty report template**

```markdown
# Live contract verification

- Date/time (UTC):
- Sitemap status/content type:
- Article URL redacted identifier:
- Page mode:
- Selector presence (`.article-title`, `.article-date`, `#js-article-body`):
- Authorization mode: none / user-owned valid session
- Audio metadata availability: yes/no (no manifest contents)
- Result and required follow-up:
```

**Step 2: Confirm it is opt-in**

Run: `NHK_EASY_LIVE_TEST=0 nhk-easy probe --live --i-understand-live-requests`

Expected: exit code 2 with an explanation that live tests are disabled.

**Step 3: Run at most the minimal permitted probe**

After the operator has reviewed current NHK terms and has a valid, legitimate session if needed, run the documented `probe` once. It may inspect sitemap and one page only; it must not download audio, save full text, save cookies, or run automatically.

**Step 4: Record only structural results and run full offline suite**

Run: `python -m pytest -q && ruff check . && mypy src`

Expected: all offline verification passes. If the selector or authorization behavior changed, do not patch around it in the same session: open an issue with the redacted evidence and return to the design review.

**Step 5: Commit the redacted report only if it contains no source content or credentials**

```bash
git add docs/verification CHANGELOG.md
git commit -m "docs: record redacted source contract verification"
```

## Final verification checklist

- [ ] `git status --short` shows no generated content, cookies, SQLite, or audio.
- [ ] `python -m pytest -q`, `ruff check .`, and `mypy src` pass.
- [ ] CI runs the same offline gate on a clean environment.
- [ ] A code review verifies that 401/403/429 stop safely and that `NEXT_PARTIAL` is never labelled complete.
- [ ] README and CLI help state personal-study / no-redistribution limits.
- [ ] No Action workflow publishes, caches, commits, or uploads NHK content.
- [ ] Any live validation is consciously opt-in, minimized, redacted, and compliant with current NHK terms.
