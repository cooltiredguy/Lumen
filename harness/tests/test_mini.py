from harness.runner.mini import remote_cmd


def test_remote_cmd_loads_brew_env():
    out = remote_cmd("/opt/homebrew", "cmake --version")
    assert 'eval "$(/opt/homebrew/bin/brew shellenv)"' in out
    assert out.strip().endswith("cmake --version")


def test_remote_cmd_is_single_shell_string():
    out = remote_cmd("/opt/homebrew", "brew --prefix boost")
    assert "\n" not in out  # one-liner safe to pass to `ssh host '<cmd>'`
