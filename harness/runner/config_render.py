def render_sunshine_conf(min_log_level: int, log_file: str) -> str:
    return "\n".join([
        "# Lumen harness config (generated)",
        "audio_sink = system",
        "virtual_display = enabled",
        "upnp = disabled",                 # deterministic local runs; no NAT noise
        f"min_log_level = {min_log_level}",
        f"log_file = {log_file}",
        "",
    ])
