#!/usr/bin/env python3
"""Explicit, bounded Jev comparison using one existing macOS Keychain item.

`status` checks metadata only. Only the explicit `compare` action reads the key,
then replaces this process with Norn's existing official adapter. No arbitrary
child command, credential export, setup prompt or automatic API test is offered.
The trusted comparison process can access the key in its environment.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import pwd
import shlex
import subprocess
import sys

SERVICE = "Norn.typesafe-api-key"
KEY_ENV = "TYPESAFE_API_KEY"
MODEL = "jev-1.13.0"
SECURITY = "/usr/bin/security"
ROOT = Path(__file__).resolve().parents[1]
LOOKUP_TIMEOUT = 30


class LauncherError(Exception):
    """Only constant, non-secret messages belong in these exceptions."""


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print("Invalid arguments. Use --help; never supply a key in arguments.", file=sys.stderr)
        raise SystemExit(2) from None


def _ensure_macos():
    if sys.platform != "darwin":
        raise LauncherError("This launcher requires macOS Keychain.")


def _environment():
    env = dict(os.environ)
    env.pop(KEY_ENV, None)
    return env


def _security(command, *, capture):
    try:
        return subprocess.run(command, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                              stderr=subprocess.PIPE, env=_environment(), timeout=LOOKUP_TIMEOUT,
                              check=False)
    except subprocess.TimeoutExpired:
        raise LauncherError("Keychain lookup timed out; check access locally.") from None
    except OSError:
        raise LauncherError("Keychain lookup could not start.") from None


def _lookup(*, password):
    _ensure_macos()
    try:
        account = pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, OSError):
        raise LauncherError("Cannot resolve the current OS account.") from None
    if not account:
        raise LauncherError("Cannot resolve the current OS account.")
    default = _security([SECURITY, "default-keychain", "-d", "user"], capture=True)
    if default.returncode:
        raise LauncherError("The default user Keychain is unavailable.")
    try:
        paths = shlex.split(default.stdout.decode("utf-8"))
    except (AttributeError, UnicodeError, ValueError):
        raise LauncherError("Cannot resolve the default user Keychain.") from None
    if len(paths) != 1 or not os.path.isabs(paths[0]) or any(c in paths[0] for c in "\x00\r\n"):
        raise LauncherError("Cannot resolve the default user Keychain.")
    command = [SECURITY, "find-generic-password", "-a", account, "-s", SERVICE]
    if password:
        command.append("-w")
    command.append(paths[0])
    return _security(command, capture=password)


def status():
    result = _lookup(password=False)
    if result.returncode:
        print("ENTRY_UNAVAILABLE_METADATA_ONLY: item may be inaccessible in this environment.")
        return 1
    print("ENTRY_PRESENT_METADATA_ONLY: saved; secret unread; API validity untested.")
    return 0


def load_key():
    result = _lookup(password=True)
    if result.returncode:
        raise LauncherError("Stored key is unavailable; check Keychain access locally.")
    try:
        key = result.stdout.decode("utf-8").removesuffix("\n")
    except (AttributeError, UnicodeError):
        raise LauncherError("Stored key has invalid text encoding.") from None
    if not key or key != key.strip() or any(c in key for c in "\x00\r\n"):
        raise LauncherError("Stored key must be one nonempty line.")
    return key


def run_compare(args):
    _ensure_macos()
    # Validate all operator choices before reading the secret. Existing compare
    # also reserves both output files before any potentially paid dispatch.
    if (bool(args.path) == bool(args.fixture) or type(args.train_rounds) is not int
            or args.train_rounds < 1 or type(args.max_decisions) is not int
            or not 1 <= args.max_decisions <= 16):
        raise LauncherError("Choose one input, positive training rounds, and at most 16 decisions.")
    if args.path and not args.path.is_file():
        raise LauncherError("The explicit journal file is unavailable.")
    if args.output.resolve() == args.html.resolve():
        raise LauncherError("JSON and HTML destinations must differ.")
    for path in (args.output, args.html):
        if path.exists() or not path.parent.is_dir():
            raise LauncherError("Outputs need distinct new files in existing directories.")
    child = [sys.executable, "-m", "norn", "compare"]
    child += [str(args.path.resolve())] if args.path else ["--fixture"]
    child += ["--train-rounds", str(args.train_rounds), "--output", str(args.output.resolve()),
              "--html", str(args.html.resolve()), "--language", args.language,
              "--decision-provider", "typesafe", "--decision-model", MODEL,
              "--decision-input", "numeric", "--max-decision-calls", "1",
              "--max-decisions", str(args.max_decisions)]
    key = load_key()
    environment = _environment()
    environment[KEY_ENV] = key
    try:
        os.chdir(ROOT)
        os.execve(sys.executable, child, environment)
    except OSError:
        raise LauncherError("Could not launch the bounded comparison; no key was printed.") from None


def main(argv=None):
    parser = SafeParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True, parser_class=SafeParser)
    commands.add_parser("status", help="metadata only; no secret read or API request")
    compare = commands.add_parser("compare", help="explicitly run one potentially paid Jev batch")
    compare.add_argument("path", nargs="?", type=Path)
    compare.add_argument("--fixture", action="store_true")
    compare.add_argument("--train-rounds", type=int, required=True)
    compare.add_argument("--output", type=Path, required=True)
    compare.add_argument("--html", type=Path, required=True)
    compare.add_argument("--language", choices=("zh", "en"), default="zh")
    compare.add_argument("--max-decisions", type=int, choices=range(1, 17), default=16)
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            return status()
        run_compare(args)
        return 0
    except LauncherError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
