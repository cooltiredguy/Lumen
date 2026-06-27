def render_sunshine_conf(min_log_level: int) -> str:
    # NOTE: `log_file` is NOT a recognized Lumen config key (it warns). The harness
    # captures logs via shell redirect in session.launch(), so we don't set it here.
    return "\n".join([
        "# Lumen harness config (generated)",
        "audio_sink = system",
        "virtual_display = enabled",
        "upnp = disabled",                 # deterministic local runs; no NAT noise
        f"min_log_level = {min_log_level}",
        "",
    ])
