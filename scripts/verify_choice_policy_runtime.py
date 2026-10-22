"""Offline, bounded CPython/Pyodide/wheel checks for the guarded v5 policy.

Uses an already installed local Pyodide package; never installs dependencies.
The repeated-card rows are a mathematical implementation check, not a visitor
journal or held-out quality estimate. Historical fixtures are never rewritten.
"""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile


GOLDEN_V4 = "sha256:429ce269da37b7f484d92aa042c6450ef5e644775bac93b7510033324bb78c3f"


def digest(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def probe():
    from norn.benchmark import default_journal
    from norn.core import engine_module
    from norn.events import content_hash
    from norn.fast_thinkers import LocalPredictor
    from norn.profile import build_event_profile, export_replay, replay_bundle

    base, policy, numeric = (engine_module(name) for name in ("choice_model", "choice_policy", "numeric"))
    catalog = base.load_catalog()
    journal = default_journal()
    rows = base.observations(journal)
    stable = [{"round": number, "shown": ["NORN_0001", "NORN_0009"],
               "picks": ["NORN_0001"], "stopped": True, "duration_ms": 3000}
              for number in range(1, 33)]

    def snapshot(case_rows, expected_status):
        unchanged = deepcopy(case_rows)
        state = policy.fit_policy(case_rows, catalog)
        trajectory = policy.trajectory(case_rows, catalog)
        assert unchanged == case_rows
        assert state["guard"]["status"] == expected_status
        assert trajectory["states"][-1]["guard"] == state["guard"]
        effective = state["effective_theta"]
        scores = base.card_scores(effective, catalog)
        predictor = LocalPredictor(dict(zip(catalog.ids, scores)), effective[-1], {}, 0)
        contributions = base.contributions(effective, state["covariance"], {"NORN_0001": ["selected"]}, catalog)
        assert base.verify_contributions(contributions, catalog)
        return {"rounds": len(case_rows), "state_hash": content_hash(state),
                "trajectory_hash": content_hash(trajectory), "guard": state["guard"],
                "candidate_theta": state["theta"], "candidate_covariance": state["covariance"],
                "effective_theta": effective,
                "predictions": predictor.predict(["NORN_0001", "NORN_0009"]),
                "contributions_hash": content_hash(contributions),
                "sequence_loss": base._log_loss(effective, base.Prepared(case_rows, catalog))[0]}

    contracts = {}
    for contract in (numeric.NATIVE_CONTRACT, numeric.PORTABLE_CONTRACT):
        with numeric.use(contract):
            contracts[contract] = {"prefix8": snapshot(rows[:8], "baseline"),
                                   "stable32_math_rows": snapshot(stable, "active")}
    bundle = export_replay(journal)
    assert bundle["profile"]["schema_version"] == "norn.profile.v5"
    assert bundle["profile"]["analysis"]["preference"]["model_version"] == policy.MODEL_VERSION
    assert replay_bundle(json.loads(json.dumps(bundle))) == bundle["profile"]
    old = build_event_profile(journal, algorithm_version="norn.scoring.v4")
    assert old["profile_hash"] == GOLDEN_V4
    assert replay_bundle({"schema_version": "norn.replay.v1", "journal": journal, "profile": old}) == old
    return {"runtime": {"python": sys.version.split()[0], "platform": platform.platform()},
            "checks": {"profile5": {"profile_hash": bundle["profile"]["profile_hash"],
                                      "canonical_hash": content_hash(bundle["profile"]), "exact_replay": True},
                       "profile4": {"profile_hash": old["profile_hash"], "golden_unchanged": True,
                                    "exact_replay": True},
                       "contracts": contracts}}


JS_PROBE = r"""
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import http from 'node:http';
import https from 'node:https';
try {
const [modulePath, source, output] = process.argv.slice(2);
const noNetwork = () => { throw new Error('Network is disabled for this verification'); };
globalThis.fetch = noNetwork;
http.request = http.get = https.request = https.get = noNetwork;
const {loadPyodide} = await import(pathToFileURL(modulePath).href);
const pyodide = await loadPyodide({indexURL: path.dirname(modulePath)});
async function copyDirectory(from, to) {
  pyodide.FS.mkdirTree(to);
  for (const entry of await fs.readdir(from, {withFileTypes: true})) {
    if (entry.name === '__pycache__') continue;
    const input = path.join(from, entry.name), target = `${to}/${entry.name}`;
    if (entry.isDirectory()) await copyDirectory(input, target);
    else if (entry.name.endsWith('.py') || entry.name.endsWith('.json'))
      pyodide.FS.writeFile(target, await fs.readFile(input));
  }
}
await copyDirectory(path.join(source, 'norn'), '/project/norn');
await copyDirectory(path.join(source, 'the-norn-machine/backend'), '/project/the-norn-machine/backend');
pyodide.FS.mkdirTree('/project/scripts');
pyodide.FS.writeFile('/project/scripts/verify_choice_policy_runtime.py',
                   await fs.readFile(path.join(source, 'scripts/verify_choice_policy_runtime.py')));
await pyodide.runPythonAsync(`import sys
sys.argv=['/project/scripts/verify_choice_policy_runtime.py','--probe-only','--repo','/project','--output','/result.json']
exec(compile(open(sys.argv[0]).read(),sys.argv[0],'exec'),{'__name__':'__main__','__file__':sys.argv[0]})`);
await fs.writeFile(output, pyodide.FS.readFile('/result.json'));
console.log(JSON.stringify({pyodide_version: pyodide.version, wasm_heap_bytes: pyodide._module.HEAPU8.byteLength,
                            network_disabled: true}));
} catch (error) {
  console.error(String(error));
  process.exitCode = 1;
}
"""


ISOLATED_WHEEL_SMOKE = r"""
import importlib.util
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
import norn
from norn.benchmark import default_journal
from norn.cli import doctor_report
from norn.core import engine_module, web_root
from norn.service import NornService
assert importlib.util.find_spec('numpy') is None
assert importlib.util.find_spec('pytest') is None
assert not Path(norn.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
doctor = doctor_report()
assert doctor['ok'] and doctor['network_used'] is False
root = web_root()
manifest = json.loads((root / 'data/cards.json').read_text())
assert len(manifest) == 256
for card in manifest:
    path = root / urlsplit(card['url']).path.lstrip('/')
    assert path.resolve().is_relative_to(root.resolve())
    raw = path.read_bytes()
    assert raw[:4] == b'RIFF' and raw[8:12] == b'WEBP'
preview = NornService().preview({'journal': default_journal(), 'compact': True})
assert preview['profile']['schema_version'] == 'norn.profile.v5'
assert preview['preference']['model_version'] == 'norn.choice.guarded-mnl.v1'
assert preview['calls'] == []
print(json.dumps({'norn_path': norn.__file__, 'choice_policy_path': engine_module('choice_policy').__file__,
                  'doctor_ok': True, 'direct_service_preview_v5': True, 'offline_images_verified': len(manifest),
                  'model_calls': 0}))
"""


def execute(arguments, *, cwd, timeout=180, env=None):
    started = time.perf_counter()
    result = subprocess.run(arguments, cwd=cwd, text=True, capture_output=True, timeout=timeout, env=env)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {arguments!r}\n{result.stderr[-6000:]}")
    return {"elapsed_seconds": time.perf_counter() - started, "stdout": result.stdout.strip()}


def max_difference(left, right):
    if isinstance(left, list):
        return max((max_difference(a, b) for a, b in zip(left, right)), default=0.0)
    return abs(left - right)


def numeric_differences(left, right):
    return {name: {key: max_difference(left[name][key], right[name][key])
                   for key in ("candidate_theta", "candidate_covariance", "effective_theta")}
            for name in ("prefix8", "stable32_math_rows")}


def verify(repo, module_path, *, wheel=True, build_backend_path=None):
    script = Path(__file__).resolve()
    runtime = module_path.parent
    package = json.loads((runtime / "package.json").read_text())
    with tempfile.TemporaryDirectory(prefix="norn-choice-policy-runtime-") as directory:
        temporary = Path(directory)
        source = temporary / "source"
        source.mkdir()
        # A single immutable source snapshot feeds CPython, WASM and the wheel.
        for relative in ("norn", "the-norn-machine/backend", "the-norn-machine/frontend"):
            shutil.copytree(repo / relative, source / relative,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("pyproject.toml", "README.md", "LICENSE"):
            if (repo / name).is_file():
                shutil.copyfile(repo / name, source / name)
        (source / "scripts").mkdir()
        shutil.copyfile(script, source / "scripts" / script.name)
        hashes = {str(path.relative_to(source)): digest(path.read_bytes()) for path in sorted(source.rglob("*"))
                  if path.is_file() and (path.suffix in (".py", ".json", ".toml") or path.name == "README.md")}
        native_path = temporary / "cpython.json"
        native_run = execute([sys.executable, str(source / "scripts" / script.name), "--probe-only",
                              "--repo", str(source), "--output", str(native_path)], cwd=temporary)
        native = json.loads(native_path.read_text())
        loader = temporary / "probe.mjs"
        loader.write_text(JS_PROBE)
        wasm_path = temporary / "pyodide.json"
        wasm_run = execute([shutil.which("node") or "node", str(loader), str(module_path), str(source), str(wasm_path)],
                           cwd=temporary)
        wasm = json.loads(wasm_path.read_text())
        portable, libm = "norn.numeric.decimal34.v1", "norn.numeric.native-libm.v1"
        assert native["checks"]["profile5"] == wasm["checks"]["profile5"]
        assert native["checks"]["profile4"] == wasm["checks"]["profile4"]
        assert canonical(native["checks"]["contracts"][portable]) == canonical(wasm["checks"]["contracts"][portable])
        result = {"schema_version": "norn.choice-policy-runtime-verification.v1", "network_used": False,
                  "real_model_calls": 0, "source_hashes": hashes, "source_manifest_hash": digest(canonical(hashes).encode()),
                  "source_snapshot": "All checks use one frozen temporary copy; hashes describe that execution snapshot, not a later full working tree.",
                  "protocol": {"fixture": "default_journal: prefix8 policy and full24 v5/v4 profiles",
                               "active_case": "32 fixed mathematical rows: choose NORN_0001 over NORN_0009, then STOP",
                               "scope": "Numerical and packaging verification; not a preference quality experiment."},
                  "runtime": {"cpython": native["runtime"], "pyodide_python": wasm["runtime"],
                              "pyodide_module": str(module_path), "pyodide_package_version": package["version"],
                              "pyodide_artifact_hashes": {name: digest((runtime / name).read_bytes()) for name in
                                  ("pyodide.mjs", "pyodide.asm.wasm", "python_stdlib.zip")},
                              "node": shutil.which("node"), "pyodide_loader": json.loads(wasm_run["stdout"].splitlines()[-1])},
                  "portable_cpython_pyodide_exact": True,
                  "profile5_exact_cross_runtime": True, "profile4_golden_exact_cross_runtime": True,
                  "native_vs_portable_max_abs": numeric_differences(native["checks"]["contracts"][libm],
                                                                     native["checks"]["contracts"][portable]),
                  "native_cpython_pyodide_max_abs": numeric_differences(native["checks"]["contracts"][libm],
                                                                         wasm["checks"]["contracts"][libm]),
                  "checks": native["checks"], "elapsed_seconds": {"cpython": native_run["elapsed_seconds"],
                                                                              "pyodide": wasm_run["elapsed_seconds"]}}
        if wheel:
            try:
                import venv

                distribution = temporary / "dist"
                build_environment = ({"PYTHONPATH": str(build_backend_path) + ":" + str(build_backend_path / "setuptools/_vendor"),
                                      "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin"}
                                     if build_backend_path else None)
                build_run = execute([sys.executable, "-m", "build", "--no-isolation", "--wheel", "--outdir", str(distribution)],
                                    cwd=source, env=build_environment)
                built = next(distribution.glob("*.whl"))
                with zipfile.ZipFile(built) as archive:
                    assert "norn/engine/choice_policy.py" in archive.namelist()
                    assert archive.read("norn/engine/choice_policy.py") == (source / "the-norn-machine/backend/choice_policy.py").read_bytes()
                environment = temporary / "venv"
                venv.EnvBuilder(with_pip=False, system_site_packages=False).create(environment)
                python = environment / "bin/python"
                execute([sys.executable, "-m", "pip", "--python", str(python), "install", "--no-index", "--no-deps", str(built)],
                        cwd=temporary)
                isolated = execute([str(python), "-I", "-c", ISOLATED_WHEEL_SMOKE, str(source)],
                    cwd=temporary)
                installed_path = temporary / "installed.json"
                installed_run = execute([str(python), "-I", str(source / "scripts" / script.name), "--probe-only",
                                         "--output", str(installed_path)], cwd=temporary)
                installed = json.loads(installed_path.read_text())
                assert canonical(installed["checks"]) == canonical(native["checks"])
                result["installed_wheel"] = {"status": "passed", "filename": built.name,
                    "sha256": digest(built.read_bytes()), "bytes": built.stat().st_size,
                    "choice_policy_packaged_exact": True, "isolated_stdlib_only": True,
                    "checks_exactly_match_source": True, "environment": json.loads(isolated["stdout"]),
                    "build_seconds": build_run["elapsed_seconds"], "probe_seconds": installed_run["elapsed_seconds"]}
                if build_backend_path:
                    result["installed_wheel"]["local_build_backend"] = {
                        "path": str(build_backend_path),
                        "build_meta_sha256": digest((build_backend_path / "setuptools/build_meta.py").read_bytes())}
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                result["installed_wheel"] = {"status": "unavailable", "reason": str(exc)}
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--pyodide-module", type=Path)
    parser.add_argument("--skip-wheel", action="store_true")
    parser.add_argument("--build-backend-path", type=Path,
                        help="Existing local package root containing setuptools/build_meta.py; installs nothing")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.probe_only:
        if args.repo:
            sys.path.insert(0, str(args.repo))
        output = probe()
    else:
        if args.pyodide_module is None:
            parser.error("Pass --pyodide-module pointing to an existing local pyodide.mjs")
        output = verify((args.repo or Path(__file__).resolve().parents[1]).resolve(),
                        args.pyodide_module.resolve(), wheel=not args.skip_wheel,
                        build_backend_path=args.build_backend_path.resolve() if args.build_backend_path else None)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "portable_exact": output.get("portable_cpython_pyodide_exact"),
                      "installed_wheel": (output.get("installed_wheel") or {}).get("status")}))
