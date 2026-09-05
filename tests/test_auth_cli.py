from typer.testing import CliRunner

from nhk_easy_fetcher.cli import app

runner = CliRunner()


class FakeCapture:
    def __init__(self, jar):
        self._jar = jar

    def run_capture(self, *a, **k):
        self._jar.write_text("{}")
        return self._jar


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


def test_auth_capture_browser_hint_renders_literally(tmp_path, monkeypatch) -> None:
    import nhk_easy_fetcher.cli as cli_mod
    from nhk_easy_fetcher.errors import BrowserUnavailable

    hint = "Playwright is not installed. Run: pip install -e '.[browser]'"

    class NoBrowserHint:
        def run_capture(self, *a: object, **k: object) -> object:
            raise BrowserUnavailable(hint)

    monkeypatch.setattr(cli_mod, "auth_capture", NoBrowserHint())
    result = runner.invoke(app, ["auth", "capture", "--cookie-jar", str(tmp_path / "c.json")])
    assert result.exit_code == 2
    assert "[browser]" in (result.stdout + result.stderr)


def test_auth_capture_rejects_non_positive_timeout(tmp_path) -> None:
    result = runner.invoke(
        app,
        ["auth", "capture", "--cookie-jar", str(tmp_path / "c.json"), "--timeout", "0"],
    )
    assert result.exit_code == 2
    assert "greater than zero" in (result.stdout + result.stderr)
