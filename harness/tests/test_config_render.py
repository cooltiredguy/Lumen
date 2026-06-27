from harness.runner.config_render import render_sunshine_conf


def test_render_sets_verbose_logging_and_virtual_display():
    text = render_sunshine_conf(min_log_level=0, log_file="/tmp/run/sunshine.log")
    assert "min_log_level = 0" in text
    assert "virtual_display = enabled" in text
    assert "audio_sink = system" in text
    assert "log_file = /tmp/run/sunshine.log" in text
