from harness.runner.preconditions import parse_console_uid, is_aqua, missing_deps


def test_parse_console_uid():
    assert parse_console_uid("501\n") == 501


def test_is_aqua_true():
    assert is_aqua("Aqua\n") is True


def test_is_aqua_false_for_background():
    assert is_aqua("Background\n") is False


def test_missing_deps_flags_absent_packages():
    # `brew list --versions X` prints a line if present, nothing if absent
    listing = {"boost": "", "llvm": "", "cmake": "cmake 4.2.3"}
    assert set(missing_deps(listing)) == {"boost", "llvm"}
