from harness.runner.mini import rsync_excludes


def test_rsync_excludes_cover_heavy_and_local_dirs():
    ex = rsync_excludes()
    for d in [".git/", "build/", "harness/reports/", "third-party/*/build/"]:
        assert d in ex
