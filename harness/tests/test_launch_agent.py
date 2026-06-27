from harness.runner.launch_agent import render_plist, agent_plist_path, PLIST_LABEL


def test_render_plist_has_program_env_and_log():
    p = render_plist(["/b/lumen", "/c/harness.conf"],
                     {"SUNSHINE_ASSETS_DIR": "/b/assets"}, "/c/run.log")
    assert "<string>/b/lumen</string>" in p
    assert f"<string>{PLIST_LABEL}</string>" in p          # dev.lumen.host
    assert "SUNSHINE_ASSETS_DIR" in p and "/b/assets" in p
    assert "/c/run.log" in p
    assert "<key>RunAtLoad</key><true/>" in p


def test_agent_plist_path():
    assert agent_plist_path("hazemeissa") == \
        "/Users/hazemeissa/Library/LaunchAgents/dev.lumen.host.plist"
