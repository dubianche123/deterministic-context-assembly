"""Mock-only checks of the bounded private Jev launcher.

No test may execute security, a child process, HTTP, or a real cwd change.
All credential-like strings below are synthetic and exist only in this file.
"""

import argparse
from pathlib import Path
import subprocess
import sys
import traceback
from types import SimpleNamespace

import pytest

from scripts import jev_keychain as launcher


SECRET = "synthetic-jeV-private-unit-test-key"
INHERITED = "synthetic-inherited-key-must-be-removed"
KEYCHAIN = "/Users/mock/Library/Keychains/login keychain-db"
ENSURE_MACOS = launcher._ensure_macos


def forbidden(*args, **kwargs):
    pytest.fail("A mock-only launcher test attempted a real system operation")


@pytest.fixture(autouse=True)
def no_real_system_operations(monkeypatch):
    monkeypatch.setattr(launcher.subprocess, "run", forbidden)
    monkeypatch.setattr(launcher.os, "execve", forbidden)
    monkeypatch.setattr(launcher.os, "chdir", forbidden)
    monkeypatch.setattr(launcher.os, "environ", {"PATH": "/mock/bin", launcher.KEY_ENV: INHERITED})
    account = launcher.pwd.struct_passwd(("mock-os-user", "x", 501, 20, "", "/mock/home", "/bin/zsh"))
    monkeypatch.setattr(launcher.pwd, "getpwuid", lambda uid: account)
    monkeypatch.setattr(launcher, "_ensure_macos", lambda: None)


def result(*, stdout=b"", stderr=b"", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def fake_security(monkeypatch, answer):
    calls = []

    def run(command, **kwargs):
        calls.append((list(command), kwargs))
        if command[1] == "default-keychain":
            return result(stdout=f'"{KEYCHAIN}"\n'.encode())
        return answer

    monkeypatch.setattr(launcher.subprocess, "run", run)
    return calls


def assert_secret_absent(capsys, error=None):
    captured = capsys.readouterr()
    text = captured.out + captured.err
    if error is not None:
        text += str(error) + repr(error)
        text += "".join(traceback.format_exception(type(error), error, error.__traceback__))
    assert SECRET not in text and INHERITED not in text
    return captured


def compare_args(tmp_path):
    return argparse.Namespace(path=None, fixture=True, train_rounds=8, max_decisions=16,
                              output=tmp_path / "result.json", html=tmp_path / "result.html", language="zh")


def test_launcher_identity_is_pinned_and_has_no_arbitrary_child_entry():
    assert launcher.SERVICE == "Norn.typesafe-api-key"
    assert launcher.KEY_ENV == "TYPESAFE_API_KEY"
    assert launcher.MODEL == "jev-1.13.0"
    assert launcher.SECURITY == "/usr/bin/security"
    assert launcher.ROOT == Path(launcher.__file__).resolve().parents[1]


@pytest.mark.parametrize("available", [True, False])
def test_status_is_metadata_only_with_password_output_discarded(monkeypatch, capsys, available):
    calls = fake_security(monkeypatch, result(stdout=SECRET.encode(), stderr=SECRET.encode(),
                                             returncode=0 if available else 44))
    assert launcher.status() == (0 if available else 1)
    assert len(calls) == 2
    assert calls[0][0] == [launcher.SECURITY, "default-keychain", "-d", "user"]
    lookup, options = calls[1]
    assert lookup == [launcher.SECURITY, "find-generic-password", "-a", "mock-os-user",
                      "-s", launcher.SERVICE, KEYCHAIN]
    assert all("-w" not in command for command, _ in calls)
    assert calls[0][1]["stdout"] == subprocess.PIPE
    assert options["stdout"] == subprocess.DEVNULL
    for command, kwargs in calls:
        assert not kwargs.get("shell", False)
        assert kwargs["timeout"] == launcher.LOOKUP_TIMEOUT and kwargs["check"] is False
        assert launcher.KEY_ENV not in kwargs["env"]
        assert SECRET not in repr(command)
    output = assert_secret_absent(capsys)
    assert "METADATA_ONLY" in output.out


def test_load_key_captures_once_without_echo_or_argument_exposure(monkeypatch, capsys):
    calls = fake_security(monkeypatch, result(stdout=(SECRET + "\n").encode(), stderr=SECRET.encode()))
    assert launcher.load_key() == SECRET
    assert len(calls) == 2
    assert calls[1][0] == [launcher.SECURITY, "find-generic-password", "-a", "mock-os-user",
                           "-s", launcher.SERVICE, "-w", KEYCHAIN]
    assert calls[1][1]["stdout"] == subprocess.PIPE
    assert calls[1][1]["stderr"] == subprocess.PIPE
    assert all(launcher.KEY_ENV not in kwargs["env"] for _, kwargs in calls)
    assert all(SECRET not in repr(command) for command, _ in calls)
    output = assert_secret_absent(capsys)
    assert output.out == output.err == ""


@pytest.mark.parametrize("payload", [None, b"", b" \n", b" leading", b"trailing ",
                                     b"two\nlines", b"two\r\n", b"nul\x00value", b"\xff"])
def test_malformed_key_is_rejected_without_echo(monkeypatch, capsys, payload):
    monkeypatch.setattr(launcher, "_lookup", lambda **kwargs: result(stdout=payload, stderr=SECRET.encode()))
    with pytest.raises(launcher.LauncherError) as raised:
        launcher.load_key()
    assert_secret_absent(capsys, raised.value)


def test_failed_secret_lookup_never_includes_captured_vendor_output(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "_lookup", lambda **kwargs: result(
        stdout=SECRET.encode(), stderr=SECRET.encode(), returncode=44))
    with pytest.raises(launcher.LauncherError) as raised:
        launcher.load_key()
    assert_secret_absent(capsys, raised.value)


@pytest.mark.parametrize("failure", ["timeout", "oserror"])
def test_security_system_errors_have_constant_secret_free_tracebacks(monkeypatch, capsys, failure):
    def fail(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 30, output=SECRET.encode(), stderr=SECRET.encode())
        raise OSError(SECRET)

    monkeypatch.setattr(launcher.subprocess, "run", fail)
    with pytest.raises(launcher.LauncherError) as raised:
        launcher._security([launcher.SECURITY, "default-keychain", "-d", "user"], capture=True)
    assert_secret_absent(capsys, raised.value)


@pytest.mark.parametrize("payload", [None, b"", b"relative.keychain", b"/a /b", b"\xff",
                                     b'"/tmp/line\nbreak"', b'"/tmp/nul\x00path"'])
def test_default_keychain_metadata_must_resolve_one_absolute_safe_path(monkeypatch, capsys, payload):
    calls = []

    def metadata(command, *, capture):
        calls.append(command)
        assert command[1] == "default-keychain"
        return result(stdout=payload, stderr=SECRET.encode())

    monkeypatch.setattr(launcher, "_security", metadata)
    with pytest.raises(launcher.LauncherError) as raised:
        launcher._lookup(password=False)
    assert len(calls) == 1
    assert_secret_absent(capsys, raised.value)


@pytest.mark.parametrize("explicit_path", [False, True])
def test_valid_compare_executes_only_the_fixed_bounded_norn_command(monkeypatch, tmp_path, capsys, explicit_path):
    args = compare_args(tmp_path)
    args.output, args.html = tmp_path / "result with space.json", tmp_path / "page with space.html"
    args.language, args.max_decisions = "en", 7
    if explicit_path:
        args.path = tmp_path / "journal with space.json"
        args.path.write_text("{}")
        args.fixture = False
    events = []
    invocation = {}

    def load():
        events.append("load-key")
        return SECRET

    def change_directory(path):
        events.append("chdir")
        assert path == launcher.ROOT

    def execute(executable, command, environment):
        events.append("execve")
        invocation.update(executable=executable, command=command, environment=environment)

    monkeypatch.setattr(launcher, "load_key", load)
    monkeypatch.setattr(launcher.os, "chdir", change_directory)
    monkeypatch.setattr(launcher.os, "execve", execute)
    expected_environment = dict(launcher.os.environ)
    expected_environment[launcher.KEY_ENV] = SECRET
    launcher.run_compare(args)
    expected = [sys.executable, "-m", "norn", "compare"]
    expected += [str(args.path.resolve())] if explicit_path else ["--fixture"]
    expected += ["--train-rounds", "8", "--output", str(args.output.resolve()), "--html", str(args.html.resolve()),
                 "--language", "en", "--decision-provider", "typesafe", "--decision-model", "jev-1.13.0",
                 "--decision-input", "numeric", "--max-decision-calls", "1", "--max-decisions", "7"]
    assert events == ["load-key", "chdir", "execve"]
    assert invocation["executable"] == sys.executable and invocation["command"] == expected
    assert invocation["environment"] == expected_environment
    assert INHERITED not in invocation["environment"].values()
    assert SECRET not in repr(invocation["command"])
    assert_secret_absent(capsys)


@pytest.mark.parametrize("invalid", ["neither_input", "both_inputs", "missing_input", "zero_train",
    "bool_train", "string_train", "zero_decisions", "too_many_decisions", "bool_decisions",
    "same_destinations", "existing_output", "existing_html", "missing_output_parent", "missing_html_parent"])
def test_compare_validation_precedes_any_secret_read_or_execution(monkeypatch, tmp_path, invalid):
    args = compare_args(tmp_path)
    if invalid == "neither_input":
        args.fixture = False
    elif invalid == "both_inputs":
        args.path = tmp_path / "input.json"
        args.path.write_text("{}")
    elif invalid == "missing_input":
        args.fixture, args.path = False, tmp_path / "absent.json"
    elif invalid in ("zero_train", "bool_train", "string_train"):
        args.train_rounds = {"zero_train": 0, "bool_train": True, "string_train": "8"}[invalid]
    elif invalid in ("zero_decisions", "too_many_decisions", "bool_decisions"):
        args.max_decisions = {"zero_decisions": 0, "too_many_decisions": 17, "bool_decisions": True}[invalid]
    elif invalid == "same_destinations":
        args.html = args.output
    elif invalid in ("existing_output", "existing_html"):
        getattr(args, "output" if invalid == "existing_output" else "html").write_text("preserve")
    elif invalid == "missing_output_parent":
        args.output = tmp_path / "missing" / "out.json"
    else:
        args.html = tmp_path / "missing" / "page.html"
    monkeypatch.setattr(launcher, "load_key", forbidden)
    with pytest.raises(launcher.LauncherError):
        launcher.run_compare(args)


def test_exec_failure_is_sanitized_and_never_echoes_key(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(launcher, "load_key", lambda: SECRET)
    monkeypatch.setattr(launcher.os, "chdir", lambda path: None)

    def fail(*args):
        raise OSError(SECRET)

    monkeypatch.setattr(launcher.os, "execve", fail)
    with pytest.raises(launcher.LauncherError) as raised:
        launcher.run_compare(compare_args(tmp_path))
    assert_secret_absent(capsys, raised.value)


def test_main_status_never_requests_the_secret(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(launcher, "status", lambda: calls.append("status") or 0)
    monkeypatch.setattr(launcher, "load_key", forbidden)
    assert launcher.main(["status"]) == 0
    assert calls == ["status"]
    assert_secret_absent(capsys)


@pytest.mark.parametrize("arguments", [
    [SECRET], ["status", "--key", SECRET], ["compare", "--fixture", "--train-rounds", SECRET],
    ["compare", "--fixture", "--train-rounds", "8", "--output", "o", "--html", "h", "--language", SECRET],
    ["compare", "--fixture", "--train-rounds", "8", "--output", "o", "--html", "h", "--command", SECRET],
    ["compare", "--fixture", "--train-rounds", "8", "--output", "o", "--html", "h", "--max-decisions", "17"],
    ["compare", "--fixture", "--train-rounds", "8", "--output", "o", "--html", "h", "--decision-model", SECRET],
    ["compare", "--fixture", "--train-rounds", "8", "--output", "o", "--html", "h", "--decision-provider", SECRET],
    ["compare", "--fixture"],
])
def test_safe_parser_rejects_extra_commands_and_never_repeats_secret_like_arguments(monkeypatch, capsys, arguments):
    monkeypatch.setattr(launcher, "load_key", forbidden)
    with pytest.raises(SystemExit) as raised:
        launcher.main(arguments)
    assert raised.value.code == 2
    output = assert_secret_absent(capsys, raised.value)
    assert "Invalid arguments" in output.err


def test_main_compare_injects_no_secret_into_argument_parsing(monkeypatch, tmp_path, capsys):
    observed = []
    monkeypatch.setattr(launcher, "run_compare", lambda args: observed.append(args))
    assert launcher.main(["compare", "--fixture", "--train-rounds", "8", "--output", str(tmp_path / "out.json"),
                          "--html", str(tmp_path / "out.html")]) == 0
    assert len(observed) == 1 and observed[0].max_decisions == 16
    assert observed[0].fixture and observed[0].path is None
    assert_secret_absent(capsys)


def test_unsupported_platform_fails_before_any_system_lookup(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "_ensure_macos", ENSURE_MACOS)
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    assert launcher.main(["status"]) == 1
    output = assert_secret_absent(capsys)
    assert "requires macOS" in output.err
