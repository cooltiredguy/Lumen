import pytest
from unittest.mock import patch, MagicMock
import subprocess

# We test run.py's three public functions: pair(), stream(), quit_stream()
# patch subprocess.run and requests.post so no real network calls happen

from harness.client import run as client_run


def test_pair_posts_pin_to_lumen(tmp_path):
    """pair() must POST a numeric code to the Lumen web API endpoint."""
    with patch("subprocess.Popen") as mock_popen, \
         patch("requests.post") as mock_post, \
         patch("time.sleep"):
        proc = MagicMock()
        proc.wait.return_value = 0
        mock_popen.return_value = proc
        mock_post.return_value = MagicMock(status_code=200, ok=True, json=lambda: {"status": True}, text="ok")

        client_run.pair(
            host="mac-mini",
            moonlight_bin="/fake/Moonlight",
            lumen_url="https://mac-mini:47990",
            admin_user="admin",
            admin_password="secret",
            pin="7777",
        )

        # subprocess.Popen called with 'pair' subcommand and --pin flag
        popen_args = mock_popen.call_args[0][0]
        assert "pair" in popen_args
        assert "--pin" in popen_args
        assert "7777" in popen_args

        # requests.post called with /api/pin and correct JSON
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "/api/pin" in call_kwargs[0][0]
        body = call_kwargs[1]["json"]
        assert body["pin"] == "7777"


def test_stream_sets_env_vars():
    """stream() must pass MOONLIGHT_TRACE_FILE and MOONLIGHT_TRACE_TOPOLOGY as env."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        client_run.stream(
            host="mac-mini",
            moonlight_bin="/fake/Moonlight",
            app="Desktop",
            resolution="1920x1080",
            fps=60,
            bitrate_kbps=20000,
            stream_seconds=5,
            trace_file="/tmp/client.jsonl",
            run_id="run1",
            topology="wifi",
        )
        env = mock_run.call_args[1]["env"]
        assert env["MOONLIGHT_TRACE_FILE"] == "/tmp/client.jsonl"
        assert env["MOONLIGHT_TRACE_TOPOLOGY"] == "wifi"
        assert env["MOONLIGHT_TRACE_RUN_ID"] == "run1"


def test_quit_stream_invokes_quit_subcommand():
    """quit_stream() must call moonlight quit <host>."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        client_run.quit_stream(host="mac-mini", moonlight_bin="/fake/Moonlight")
        args = mock_run.call_args[0][0]
        assert "quit" in args
        assert "mac-mini" in args
