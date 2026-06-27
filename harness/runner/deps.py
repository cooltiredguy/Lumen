from .mini import run_remote
from .preconditions import check_deps


def ensure_deps(ssh_host: str, brew_prefix: str) -> list[str]:
    missing = check_deps(ssh_host, brew_prefix)
    if missing:
        run_remote(ssh_host, brew_prefix, "brew install " + " ".join(missing), timeout=3600)
    return check_deps(ssh_host, brew_prefix)  # should be [] after install
