import os
import subprocess
from pathlib import Path


def test_fetch_latest_script_exists_and_executable() -> None:
    script = Path(__file__).parents[1] / "scripts" / "fetch-latest.sh"
    assert script.exists(), "scripts/fetch-latest.sh must exist"
    assert os.access(script, os.X_OK), "scripts/fetch-latest.sh must be executable"


def test_fetch_latest_script_covers_one_click_flow() -> None:
    script = Path(__file__).parents[1] / "scripts" / "fetch-latest.sh"
    text = script.read_text(encoding="utf-8")
    # 自检: ffmpeg + 依赖安装
    assert "ffmpeg" in text
    assert "pip install" in text
    # Cookie 半自动: 受限权限；同意页由 auth capture 模块统一管理
    assert "cookies.json" in text
    assert "chmod 600" in text
    # 一键抓: 调用现有 CLI，默认 m4a + cookie_jar
    assert "fetch-latest" in text
    assert "--audio" in text
    assert "cookie_jar" in text or "--cookie-jar" in text
    # 可覆盖输出目录/音频格式
    assert "OUTPUT_DIR" in text or "--output" in text
    assert "AUDIO_MODE" in text or "--audio" in text


def test_fetch_latest_script_fails_closed_without_sharing_secrets() -> None:
    script = Path(__file__).parents[1] / "scripts" / "fetch-latest.sh"
    text = script.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    # 不得要求密码/保存密码
    lowered = text.lower()
    assert "password" not in lowered


def test_fetch_latest_script_uses_auth_capture() -> None:
    from pathlib import Path

    text = (Path(__file__).parents[1] / "scripts" / "fetch-latest.sh").read_text(encoding="utf-8")
    assert "auth capture" in text
    assert "nhk-easy auth capture --cookie-jar" in text


def test_fetch_latest_script_installs_browser_extra_and_allows_text_only() -> None:
    text = (Path(__file__).parents[1] / "scripts" / "fetch-latest.sh").read_text(
        encoding="utf-8"
    )
    assert "'[browser]'" in text
    assert 'if [[ "$AUDIO_MODE" != "off" ]]' in text


def test_fetch_latest_script_text_only_needs_no_cookie_or_ffmpeg(tmp_path: Path) -> None:
    script = Path(__file__).parents[1] / "scripts" / "fetch-latest.sh"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log = tmp_path / "calls.txt"
    fake_cli = fake_bin / "nhk-easy"
    fake_cli.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$CALL_LOG"\n', encoding="utf-8")
    fake_cli.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(tmp_path),
        "OUTPUT_DIR": str(tmp_path / "output"),
        "COOKIE_JAR": str(tmp_path / "missing.json"),
        "AUDIO_MODE": "off",
        "CALL_LOG": str(call_log),
    }
    completed = subprocess.run([str(script)], env=env, text=True, capture_output=True)

    assert completed.returncode == 0, completed.stderr
    assert call_log.read_text(encoding="utf-8").strip().startswith("fetch-latest --audio off")
    assert "cookie-jar" not in call_log.read_text(encoding="utf-8")
    assert not (tmp_path / "missing.json").exists()
