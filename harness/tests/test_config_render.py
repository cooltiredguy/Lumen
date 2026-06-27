from harness.runner.config_render import render_sunshine_conf


def test_render_sets_verbose_logging_and_virtual_display():
    text = render_sunshine_conf(min_log_level=0)
    assert "min_log_level = 0" in text
    assert "virtual_display = enabled" in text
    assert "audio_sink = system" in text


def test_render_omits_unrecognized_log_file_key():
    # `log_file` warns as unrecognized in Lumen; harness uses shell redirect instead
    assert "log_file" not in render_sunshine_conf(min_log_level=0)
