from .mini import run_remote


def disable_sleep_lock(ssh_host: str, brew_prefix: str) -> None:
    run_remote(ssh_host, brew_prefix,
               "sudo -n pmset -a displaysleep 0 sleep 0 disablesleep 1", check=False)


def restore_sleep_lock(ssh_host: str, brew_prefix: str) -> None:
    run_remote(ssh_host, brew_prefix,
               "sudo -n pmset -a disablesleep 0 displaysleep 10 sleep 0", check=False)
