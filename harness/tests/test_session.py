from harness.runner.session import log_ready, log_capture_failed, _asuser


def test_log_ready_detects_marker():
    assert log_ready("...\nConfiguration UI available at https://...:47990\n") is True


def test_log_ready_false_when_not_yet():
    assert log_ready("Booting...\n") is False


def test_log_capture_failed_detects_sck_denial():
    assert log_capture_failed("error: SCShareableContent failed\n") is True


def test_log_capture_failed_false_on_clean():
    assert log_capture_failed("Capturing display 1\n") is False


def test_asuser_drops_to_user_in_session():
    cmd = _asuser(501, "hazemeissa", "launchctl managername")
    assert cmd == "sudo -n launchctl asuser 501 sudo -u hazemeissa launchctl managername"


def test_launch_plist_includes_trace_env_vars():
    from harness.runner.launch_agent import render_plist
    plist = render_plist(
        ["/b/lumen", "/c/harness.conf"],
        {
            "SUNSHINE_ASSETS_DIR": "/b/assets",
            "LUMEN_TRACE_FILE": "/t/trace.jsonl",
            "LUMEN_TRACE_RUN_ID": "20260626-120000",
            "LUMEN_TRACE_TOPOLOGY": "loopback",
        },
        "/c/run.log"
    )
    assert "LUMEN_TRACE_FILE" in plist
    assert "/t/trace.jsonl" in plist
    assert "LUMEN_TRACE_RUN_ID" in plist
    assert "20260626-120000" in plist
    assert "LUMEN_TRACE_TOPOLOGY" in plist
