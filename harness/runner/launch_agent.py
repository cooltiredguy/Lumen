PLIST_LABEL = "dev.lumen.host"


def agent_plist_path(user: str) -> str:
    return f"/Users/{user}/Library/LaunchAgents/{PLIST_LABEL}.plist"


def render_plist(program_args: list[str], env: dict[str, str], log_file: str) -> str:
    """A LaunchAgent plist. launchd execs the program DIRECTLY, so it is its own
    TCC 'responsible process' — Screen Recording granted to lumen actually applies
    (unlike launching via sudo/asuser/bash wrappers, which mis-attribute the grant)."""
    args = "".join(f"\n    <string>{a}</string>" for a in program_args)
    envx = "".join(f"\n    <key>{k}</key><string>{v}</string>" for k, v in env.items())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f'  <key>Label</key><string>{PLIST_LABEL}</string>\n'
        f'  <key>ProgramArguments</key><array>{args}\n  </array>\n'
        f'  <key>EnvironmentVariables</key><dict>{envx}\n  </dict>\n'
        f'  <key>StandardOutPath</key><string>{log_file}</string>\n'
        f'  <key>StandardErrorPath</key><string>{log_file}</string>\n'
        '  <key>RunAtLoad</key><true/>\n'
        '  <key>ProcessType</key><string>Interactive</string>\n'
        '</dict></plist>\n'
    )
