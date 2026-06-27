from .mini import run_remote


def build_cmake_flags(sdk_path: str, openssl_prefix: str, assets_dir: str) -> list[str]:
    cxx = "/usr/include/c++/v1"
    return [
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_WERROR=ON",
        f"-DOPENSSL_ROOT_DIR={openssl_prefix}",
        f"-DSUNSHINE_ASSETS_DIR={assets_dir}",
        "-DSUNSHINE_BUILD_HOMEBREW=ON",
        "-DSUNSHINE_ENABLE_TRAY=ON",
        "-DBOOST_USE_STATIC=OFF",
        f"-DCMAKE_OSX_SYSROOT={sdk_path}",
        f"-DCMAKE_CXX_FLAGS=-nostdinc++ -cxx-isystem {sdk_path}{cxx} -std=gnu++2b -I{openssl_prefix}/include",
        f"-DCMAKE_C_FLAGS=-I{openssl_prefix}/include",
    ]


def build(ssh_host: str, brew_prefix: str, deploy_dir: str, build_dir: str) -> str:
    sdk = run_remote(ssh_host, brew_prefix, "xcrun --show-sdk-path").stdout.strip()
    ossl = run_remote(ssh_host, brew_prefix, "brew --prefix openssl@3").stdout.strip()
    flags = build_cmake_flags(sdk, ossl, f"{build_dir}/assets")
    cfg = (f"mkdir -p {build_dir} && cd {build_dir} && "
           f"cmake {' '.join(repr(f) for f in flags)} {deploy_dir}")
    run_remote(ssh_host, brew_prefix, cfg, timeout=1800)
    make = f"cd {build_dir} && make sunshine vd_helper -j$(sysctl -n hw.ncpu)"
    run_remote(ssh_host, brew_prefix, make, timeout=3600)
    return f"{build_dir}/sunshine"
