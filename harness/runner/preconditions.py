from .mini import run_remote

REQUIRED_DEPS = ["cmake", "boost", "pkg-config", "openssl@3", "opus", "llvm",
                 "doxygen", "graphviz", "node", "icu4c@78", "miniupnpc"]


def parse_console_uid(stat_output: str) -> int:
    return int(stat_output.strip())


def is_aqua(managername_output: str) -> bool:
    return managername_output.strip() == "Aqua"


def missing_deps(listing: dict[str, str]) -> list[str]:
    """listing maps dep -> output of `brew list --versions dep` ('' if absent)."""
    return [d for d, v in listing.items() if not v.strip()]


def console_uid(ssh_host: str, brew_prefix: str) -> int:
    return parse_console_uid(run_remote(ssh_host, brew_prefix, "stat -f%u /dev/console").stdout)


def console_user_present(ssh_host: str, brew_prefix: str) -> bool:
    out = run_remote(ssh_host, brew_prefix,
                     "scutil <<< 'show State:/Users/ConsoleUser' | awk '/Name :/{print $3}'").stdout
    return out.strip() not in ("", "loginwindow")


def aqua_session_ready(ssh_host: str, brew_prefix: str, uid: int) -> bool:
    # `launchctl asuser` switches audit sessions => needs root. Passwordless sudo
    # for /bin/launchctl is provisioned via /etc/sudoers.d/lumen-harness (one-time).
    out = run_remote(ssh_host, brew_prefix,
                     f"sudo -n launchctl asuser {uid} launchctl managername").stdout
    return is_aqua(out)


def check_deps(ssh_host: str, brew_prefix: str) -> list[str]:
    listing = {}
    for d in REQUIRED_DEPS:
        out = run_remote(ssh_host, brew_prefix, f"brew list --versions {d} | head -1",
                         check=False).stdout
        listing[d] = out
    return missing_deps(listing)
