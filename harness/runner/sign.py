from .mini import run_remote

KEYCHAIN = "lumen.keychain"
KEYCHAIN_PASS = "lumen"
# Unique, stable identifier for the host binary. MUST differ from upstream's
# 'dev.lizardbyte.sunshine' so the Screen Recording (TCC) grant doesn't collide
# with the denied auto-registered sunshine-0.0.0 record. Stable id + our cert =
# a Designated Requirement with no cdhash, so the grant survives rebuilds.
IDENTIFIER = "dev.lumen.host"


def sign_binaries(ssh_host: str, brew_prefix: str, build_dir: str, identity: str) -> None:
    # unlock + partition-list + codesign MUST run in one SSH session (ACL access is
    # session-bound over SSH). lumen gets the unique identifier; vd_helper keeps its own.
    cmd = (
        f"security unlock-keychain -p {KEYCHAIN_PASS} {KEYCHAIN} && "
        f"security set-key-partition-list -S apple-tool:,apple:,unsigned: "
        f"-s -k {KEYCHAIN_PASS} {KEYCHAIN} >/dev/null 2>&1; "
        f"codesign --force --sign '{identity}' --identifier {IDENTIFIER} "
        f"--keychain {KEYCHAIN} --timestamp=none {build_dir}/lumen && "
        f"codesign --force --sign '{identity}' "
        f"--keychain {KEYCHAIN} --timestamp=none {build_dir}/vd_helper"
    )
    run_remote(ssh_host, brew_prefix, cmd)


def signed_identity(ssh_host: str, brew_prefix: str, build_dir: str) -> str:
    out = run_remote(ssh_host, brew_prefix,
                     f"codesign -d --verbose=4 {build_dir}/lumen 2>&1 "
                     f"| grep -i 'Authority=' | head -1",
                     check=False).stdout
    return out.strip()
