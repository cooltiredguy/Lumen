from harness.runner.build import build_cmake_flags


def test_cmake_flags_include_toolchain_fixes():
    flags = build_cmake_flags(sdk_path="/SDK", openssl_prefix="/ossl",
                              assets_dir="/Volumes/T7/lumen-harness/Lumen/build/assets")
    joined = " ".join(flags)
    assert "-DCMAKE_BUILD_TYPE=Release" in joined
    assert "-DOPENSSL_ROOT_DIR=/ossl" in joined
    assert "-nostdinc++" in joined and "/SDK/usr/include/c++/v1" in joined
    assert "-std=gnu++2b" in joined
    assert "-DCMAKE_OSX_SYSROOT=/SDK" in joined
