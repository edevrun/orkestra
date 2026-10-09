"""Verification gates: detection per ecosystem, baseline, verify (no-regression rule).

Regression rule (deterministic): every gate that PASSED at baseline must PASS at verify.
Gates that already failed at baseline are reported as pre-existing and never blamed on the run.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .util import now_iso, read_json, tail_lines, write_json_atomic, write_text_atomic

DEFAULT_TIMEOUT = 900


# ---------------------------------------------------------------- detection

def _pkg_manager(project: Path) -> str:
    if (project / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (project / "yarn.lock").exists():
        return "yarn"
    if (project / "bun.lockb").exists() or (project / "bun.lock").exists():
        return "bun"
    return "npm"


def _run_script(pm: str, script: str) -> str:
    if pm == "npm":
        return "npm test" if script == "test" else f"npm run {script}"
    if pm == "yarn":
        return f"yarn {script}"
    if pm == "bun":
        return f"bun run {script}"
    return f"pnpm run {script}"


def _gate(name: str, cmd: str, kind: str, timeout: int = DEFAULT_TIMEOUT, cwd: str = ".") -> Dict[str, Any]:
    return {"name": name, "cmd": cmd, "kind": kind, "timeout": timeout, "cwd": cwd}


SUBPROJECT_DIRS = ("backend", "server", "api", "web", "frontend", "mobile", "app", "client")


def detect(project: Path) -> List[Dict[str, Any]]:
    """Gates for the project root plus (for multi-stack repos) conventional sub-projects.

    Detection is a proposal: onboarding verifies it against AGENTS.md/CLAUDE.md and the user can edit
    .orkestra/config.json (set "enabled": false on slow gates).
    """
    project = Path(project)
    gates: List[Dict[str, Any]] = []
    names: set = set()

    def add(g: Dict[str, Any]) -> None:
        base = g["name"]
        n, i = base, 2
        while n in names:
            n = f"{base}-{i}"
            i += 1
        g["name"] = n
        names.add(n)
        gates.append(g)

    _detect_dir(project, ".", add)
    root_has_workspaces = bool((read_json(project / "package.json", default={}) or {}).get("workspaces")) \
        or (project / "pnpm-workspace.yaml").exists()
    if not root_has_workspaces:
        subs: List[Path] = []
        for d in SUBPROJECT_DIRS:
            if (project / d).is_dir():
                subs.append(project / d)
        for parent in ("apps", "packages", "services"):
            if (project / parent).is_dir():
                subs += sorted(p for p in (project / parent).iterdir() if p.is_dir() and not p.name.startswith("."))
        for sub in subs[:12]:
            rel = str(sub.relative_to(project))
            prefix = sub.name
            _detect_dir(sub, rel, lambda g, prefix=prefix: add(dict(g, name=f"{prefix}:{g['name']}")))
    return gates


def _detect_dir(d: Path, cwd: str, add: Any) -> None:
    pj = d / "package.json"
    if pj.exists():
        data = read_json(pj, default={}) or {}
        scripts: Dict[str, str] = data.get("scripts") or {}
        pm = _pkg_manager(d)
        tsc_script = next((s for s in ("typecheck", "type-check", "check-types", "types", "tsc") if s in scripts), None)
        if tsc_script:
            add(_gate("typecheck", _run_script(pm, tsc_script), "typecheck", cwd=cwd))
        elif (d / "tsconfig.json").exists():
            add(_gate("typecheck", "npx --no-install tsc --noEmit", "typecheck", cwd=cwd))
        if "lint" in scripts:
            add(_gate("lint", _run_script(pm, "lint"), "lint", cwd=cwd))
        test_script = "test" if scripts.get("test") and "no test specified" not in scripts.get("test", "") else \
            next((s for s in ("test:unit", "test:all", "test:web") if s in scripts), None)
        if test_script:
            add(_gate("test", _run_script(pm, test_script), "test", cwd=cwd))
        if "build" in scripts:
            add(_gate("build", _run_script(pm, "build"), "build", timeout=1800, cwd=cwd))

    if (d / "pyproject.toml").exists() or (d / "requirements.txt").exists() or (d / "setup.py").exists():
        runner = "uv run " if (d / "uv.lock").exists() else ("poetry run " if (d / "poetry.lock").exists() else "")
        py = "python3 -m " if not runner else ""
        pyproject = (d / "pyproject.toml").read_text(encoding="utf-8", errors="replace") if (d / "pyproject.toml").exists() else ""
        has_tests = any((d / x).is_dir() for x in ("tests", "test")) or "pytest" in pyproject
        if has_tests:
            add(_gate("pytest", f"{runner}{py}pytest -q", "test", cwd=cwd))
        if "[tool.ruff" in pyproject or (d / "ruff.toml").exists() or (d / ".ruff.toml").exists():
            add(_gate("ruff", f"{runner}{py}ruff check .", "lint", cwd=cwd))
        if "[tool.mypy" in pyproject or (d / "mypy.ini").exists():
            add(_gate("mypy", f"{runner}{py}mypy .", "typecheck", cwd=cwd))

    if (d / "go.mod").exists():
        add(_gate("go-build", "go build ./...", "build", cwd=cwd))
        add(_gate("go-vet", "go vet ./...", "lint", cwd=cwd))
        add(_gate("go-test", "go test ./...", "test", cwd=cwd))

    if (d / "Cargo.toml").exists():
        add(_gate("cargo-build", "cargo build --quiet", "build", timeout=1800, cwd=cwd))
        add(_gate("cargo-test", "cargo test --quiet", "test", timeout=1800, cwd=cwd))

    pub = d / "pubspec.yaml"
    if pub.exists():
        txt = pub.read_text(encoding="utf-8", errors="replace")
        tool = "flutter" if re.search(r"^\s*flutter\s*:", txt, re.M) else "dart"
        add(_gate(f"{tool}-analyze", f"{tool} analyze", "lint", cwd=cwd))
        if (d / "test").is_dir():
            add(_gate(f"{tool}-test", f"{tool} test", "test", timeout=1800, cwd=cwd))

    if (d / "pom.xml").exists():
        mvn = "./mvnw" if (d / "mvnw").exists() else "mvn"
        add(_gate("maven-test", f"{mvn} -q test", "test", timeout=2400, cwd=cwd))
    elif ((d / "build.gradle").exists() or (d / "build.gradle.kts").exists()) and (d / "gradlew").exists() and not pj.exists():
        add(_gate("gradle-test", "./gradlew test --quiet", "test", timeout=2400, cwd=cwd))

    slns = list(d.glob("*.sln"))
    if slns or list(d.glob("*.csproj")):
        add(_gate("dotnet-build", "dotnet build --nologo -v q", "build", timeout=1800, cwd=cwd))
        add(_gate("dotnet-test", "dotnet test --nologo -v q", "test", timeout=1800, cwd=cwd))
    else:
        test_projs = sorted(p for p in d.glob("*/*/*.csproj") if "test" in p.name.lower())[:3]
        for tp in test_projs:
            rel = str(tp.parent.relative_to(d))
            add(_gate("dotnet-test", f"dotnet test {rel} --nologo -v q", "test", timeout=1800, cwd=cwd))

    if (d / "Package.swift").exists():
        add(_gate("swift-build", "swift build", "build", timeout=1800, cwd=cwd))
        add(_gate("swift-test", "swift test", "test", timeout=1800, cwd=cwd))

    if (d / "composer.json").exists():
        data = read_json(d / "composer.json", default={}) or {}
        if "test" in (data.get("scripts") or {}):
            add(_gate("composer-test", "composer test", "test", cwd=cwd))

    mk = d / "Makefile"
    if mk.exists() and cwd == "." and not any((d / m).exists() for m in ("package.json", "go.mod", "Cargo.toml", "pom.xml")):
        txt = mk.read_text(encoding="utf-8", errors="replace")
        for target, kind in (("test", "test"), ("lint", "lint"), ("build", "build")):
            if re.search(rf"^{target}\s*:", txt, re.M):
                add(_gate(f"make-{target}", f"make {target}", kind, cwd=cwd))


# ---------------------------------------------------------------- running

def run_gate(project: Path, gate: Dict[str, Any], log_dir: Path, label: str) -> Dict[str, Any]:
    log_dir.mkdir(parents=True, exist_ok=True)
    cwd = (Path(project) / gate.get("cwd", ".")).resolve(strict=False)
    env = dict(os.environ)
    env.setdefault("CI", "1")
    env.setdefault("FORCE_COLOR", "0")
    env.setdefault("NO_COLOR", "1")
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    started = time.time()
    timed_out = False
    try:
        cp = subprocess.run(gate["cmd"], shell=True, cwd=str(cwd), env=env, capture_output=True,
                            timeout=int(gate.get("timeout", DEFAULT_TIMEOUT)))
        code = cp.returncode
        output = (cp.stdout or b"").decode("utf-8", "replace") + (cp.stderr or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        code = 124
        output = ((exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")) + "\n[orkestra] TIMEOUT"
    duration = round(time.time() - started, 1)
    log_path = log_dir / f"{label}-{gate['name']}.log"
    write_text_atomic(log_path, f"$ {gate['cmd']}\n(cwd={cwd})\n\n{output}\n[exit {code}]\n")
    return {
        "name": gate["name"], "cmd": gate["cmd"], "kind": gate.get("kind", "other"),
        "exit": code, "ok": code == 0, "timed_out": timed_out, "duration_s": duration,
        "log": str(log_path), "tail": tail_lines(output, 30, 3000) if code != 0 else "",
        "at": now_iso(),
    }


def run_all(project: Path, gates: List[Dict[str, Any]], log_dir: Path, label: str,
            only: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    results = []
    for g in gates:
        if only and g["name"] not in only:
            continue
        if g.get("enabled") is False and not only:
            continue
        results.append(run_gate(project, g, log_dir, label))
    return results


def compare(baseline: List[Dict[str, Any]], current: List[Dict[str, Any]]) -> Dict[str, Any]:
    b = {r["name"]: r for r in baseline}
    c = {r["name"]: r for r in current}
    regressions, fixed, pre_existing, new_fail, new_pass, missing = [], [], [], [], [], []
    for name, cur in c.items():
        if name in b:
            was = b[name]["ok"]
            if was and not cur["ok"]:
                regressions.append(name)
            elif not was and cur["ok"]:
                fixed.append(name)
            elif not was and not cur["ok"]:
                pre_existing.append(name)
        else:
            (new_pass if cur["ok"] else new_fail).append(name)
    for name in b:
        if name not in c:
            missing.append(name)
    ok = not regressions and not new_fail and not missing
    return {"ok": ok, "regressions": regressions, "fixed": fixed, "pre_existing_failures": pre_existing,
            "new_gate_failures": new_fail, "new_gate_passes": new_pass, "not_run": missing}


def format_results(results: List[Dict[str, Any]], comparison: Optional[Dict[str, Any]] = None,
                   baseline: Optional[List[Dict[str, Any]]] = None) -> str:
    bmap = {r["name"]: r for r in (baseline or [])}
    lines = ["| gate | baseline | now | süre | komut |", "|---|---|---|---|---|"]
    for r in results:
        b = bmap.get(r["name"])
        bcell = ("PASS" if b["ok"] else "FAIL") if b else "—"
        lines.append(f"| {r['name']} | {bcell} | {'PASS' if r['ok'] else ('TIMEOUT' if r['timed_out'] else 'FAIL')} "
                     f"| {r['duration_s']}s | `{r['cmd']}` |")
    out = "\n".join(lines)
    if comparison is not None:
        out += "\n\n" + ("NO REGRESSIONS" if comparison["ok"] else "REGRESSION DETECTED")
        for key in ("regressions", "new_gate_failures", "not_run", "pre_existing_failures", "fixed"):
            if comparison.get(key):
                out += f"\n- {key}: {', '.join(comparison[key])}"
    for r in results:
        if not r["ok"]:
            out += f"\n\n--- {r['name']} (exit {r['exit']}) tail — full log: {r['log']}\n{r['tail']}"
    return out


def save_results(path: Path, results: List[Dict[str, Any]], extra: Optional[Dict[str, Any]] = None) -> None:
    payload: Dict[str, Any] = {"at": now_iso(), "results": results}
    if extra:
        payload.update(extra)
    write_json_atomic(path, payload)


# ---------------------------------------------------------------- dependency manifests

def dependency_sets(project: Path) -> Dict[str, List[str]]:
    """Best-effort set of declared dependency names per manifest (for the deps check)."""
    project = Path(project)
    out: Dict[str, List[str]] = {}
    pj = read_json(project / "package.json")
    if isinstance(pj, dict):
        names = set()
        for k in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            names |= set((pj.get(k) or {}).keys())
        out["package.json"] = sorted(names)
    req = project / "requirements.txt"
    if req.exists():
        names = set()
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.split("#")[0].strip()
            if line and not line.startswith("-"):
                names.add(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].lower())
        out["requirements.txt"] = sorted(names)
    pp = project / "pyproject.toml"
    if pp.exists():
        txt = pp.read_text(encoding="utf-8", errors="replace")
        names = set()
        m = re.search(r"^dependencies\s*=\s*\[(.*?)\]", txt, re.S | re.M)
        if m:
            for item in re.findall(r"[\"']([^\"']+)[\"']", m.group(1)):
                names.add(re.split(r"[<>=!~\[; ]", item, maxsplit=1)[0].lower())
        for block in re.finditer(r"^\[tool\.poetry\.(?:dev-)?dependencies\]\s*\n(.*?)(?=^\[|\Z)", txt, re.S | re.M):
            for line in block.group(1).splitlines():
                mm = re.match(r"^([A-Za-z0-9_.\-]+)\s*=", line)
                if mm and mm.group(1).lower() != "python":
                    names.add(mm.group(1).lower())
        out["pyproject.toml"] = sorted(names)
    gm = project / "go.mod"
    if gm.exists():
        txt = gm.read_text(encoding="utf-8", errors="replace")
        names = set(re.findall(r"^\s*([a-zA-Z0-9.\-_/]+\.[a-z]+/[^\s]+)\s+v", txt, re.M))
        out["go.mod"] = sorted(names)
    ct = project / "Cargo.toml"
    if ct.exists():
        txt = ct.read_text(encoding="utf-8", errors="replace")
        names = set()
        for block in re.finditer(r"^\[(?:dev-|build-)?dependencies\]\s*\n(.*?)(?=^\[|\Z)", txt, re.S | re.M):
            for line in block.group(1).splitlines():
                mm = re.match(r"^([A-Za-z0-9_\-]+)\s*=", line)
                if mm:
                    names.add(mm.group(1))
        out["Cargo.toml"] = sorted(names)
    ps = project / "pubspec.yaml"
    if ps.exists():
        txt = ps.read_text(encoding="utf-8", errors="replace")
        names = set()
        for block in re.finditer(r"^(?:dev_)?dependencies:\s*\n((?:[ \t]+.*\n?)*)", txt, re.M):
            for line in block.group(1).splitlines():
                mm = re.match(r"^  ([A-Za-z0-9_]+)\s*:", line)
                if mm:
                    names.add(mm.group(1))
        out["pubspec.yaml"] = sorted(names)
    cj = read_json(project / "composer.json")
    if isinstance(cj, dict):
        out["composer.json"] = sorted(set((cj.get("require") or {}).keys()) | set((cj.get("require-dev") or {}).keys()))
    return out


def new_dependencies(before: Dict[str, List[str]], after: Dict[str, List[str]]) -> List[str]:
    added: List[str] = []
    for manifest, names in after.items():
        prev = set(before.get(manifest, []))
        for n in names:
            if n not in prev:
                added.append(n)
    return sorted(set(added))


def normalize_pkg(name: str) -> str:
    name = name.strip().strip("'\"")
    if name.startswith("@"):
        # scoped npm: @scope/pkg@1.2 -> @scope/pkg
        parts = name.split("@")
        return "@" + parts[1] if len(parts) > 1 else name
    name = re.split(r"[@<>=!~\[;: ]", name, maxsplit=1)[0]
    return name.lower() if not re.search(r"[A-Z]", name) else name

