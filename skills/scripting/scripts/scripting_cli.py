#!/usr/bin/env python3
"""
scripting — develop, check, deploy and run Python scripts that execute INSIDE a RipEx2 / RA2 device
under the Scripting launcher.

    scripting new NAME [--kind continuous|oneshot] [--netconf] [--out FILE]   generate a script skeleton
    scripting check FILE...                         static checks against the Scripting runtime contract
    scripting modules [--fetch] [--instance NAME]   modules importable on the target firmware (cached per firmware)
    scripting enable [--on|--off]                   show / change main.RR_Sdk_Enable (RA2 API)
    scripting deploy FILE [--name N] [--instance I] [--set LEAF=VALUE ...]   store the script + one instance (NETCONF edit-config)
    scripting undeploy NAME                         delete a script and its instances (NETCONF edit-config delete)
    scripting list                                  scripts, instances, last execution (script_overview_get)
    scripting start|stop NAME INSTANCE              manual trigger (RA2 API)
    scripting run NAME INSTANCE [--timeout S] [--no-start]   start, wait for the end, print record + output
    scripting logs NAME INSTANCE [--execution ID] [--follow]  captured output of an execution
    scripting executions NAME [INSTANCE] [--limit N]          execution history

Connection: RA2_HOST / RA2_USER / RA2_PASSWORD (+ RA2_NETCONF_PORT) from flags, environment or
a `.env.ra2` file (shared with skills/api and skills/netconf). RA2-API commands run on system
python3 (rr_ra2_mgmt is stdlib-only); deploy/undeploy need the repo .venv (rr_netconf_mgmt).
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = SKILL_DIR.parent.parent
RA2_SRC = REPO_DIR / "skills" / "api" / "ra2_mgmt" / "src"
NETCONF_SRC = REPO_DIR / "skills" / "netconf" / "rac_netconf_mgmt" / "src"
DOCS_DIR = REPO_DIR / "docs" / "scripting"
MODULES_DIR = SKILL_DIR / "references" / "modules"
TEMPLATES_DIR = SKILL_DIR / "templates"
for p in (RA2_SRC, NETCONF_SRC):
    sys.path.insert(0, str(p))

try:
    from rr_ra2_mgmt.device import Device
    from rr_ra2_mgmt.exceptions import Ra2CommunicationError, Ra2LoginError, Ra2MgmtError, Ra2RpcError
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(f"scripting: cannot import rr_ra2_mgmt from {RA2_SRC} ({exc}); run: git submodule update --init\n")
    sys.exit(1)

ENV_FILE = ".env.ra2"
LAUNCHER_NS = "eu:racom:common:rr-sdk-launcher"
NC_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"
ENABLE_KEY = ("main", "RR_Sdk_Enable")
FINAL_STATES = {"finished", "killed"}
PAGE = 1000
LIST_MODULES_SCRIPT = "_scripting_list_modules.py"
KNOWN_THIRD_PARTY = {"libyang", "lxml", "ncclient", "ssh", "sysrepo", "peewee", "cffi", "api2yang", "sdk_launcher",
                     "rr_ra2_mgmt", "rr_netconf_mgmt"}
EXIT_USAGE, EXIT_RPC, EXIT_COMM, EXIT_CHECK = 1, 2, 3, 4


# --------------------------------------------------------------------------- utilities


def die(msg: str, code: int = EXIT_USAGE) -> None:
    sys.stderr.write(f"scripting: {msg}\n")
    sys.exit(code)


def info(msg: str) -> None:
    sys.stderr.write(f"{msg}\n")


def to_jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj


def dump(obj: Any, compact: bool = False) -> None:
    sys.stdout.write(json.dumps(to_jsonable(obj), ensure_ascii=False, indent=None if compact else 2,
                                separators=(",", ":") if compact else None) + "\n")
    sys.stdout.flush()


# --------------------------------------------------------------------------- connection settings


def find_env_file() -> Path | None:
    for base in (Path.cwd(), SKILL_DIR):
        for d in [base, *base.parents]:
            if (d / ENV_FILE).is_file():
                return d / ENV_FILE
    return None


def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        k, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


@dataclasses.dataclass
class Conn:
    host: str | None                     # RA2 API peer, carries the port inside the string
    user: str
    password: str
    netconf_port: int
    timeout: int
    source: str
    netconf_host: str | None = None      # bare address; NETCONF takes the port as its own argument

    def ra2(self) -> Device:
        if not self.host:
            die("no device host: pass --host, set RA2_HOST, or create .env.ra2 (see .env.ra2.example)")
        if not (self.user and self.password):
            die("no credentials: set RA2_USER / RA2_PASSWORD", EXIT_COMM)
        return Device(self.host, self.user, self.password, timeout=self.timeout, retries=1)

    def netconf(self) -> Any:
        try:
            from rr_netconf_mgmt.device import NetconfDevice  # noqa: PLC0415
        except ImportError:
            die("deploy/undeploy need rr_netconf_mgmt (libyang, ncclient): run with <repo>/.venv/bin/python "
                "after `bash skills/netconf/scripts/setup.sh`", EXIT_USAGE)
        if not self.host:
            die("no device host: pass --host, set RA2_HOST, or create .env.ra2")
        if not (self.user and self.password):
            die("no credentials: set RA2_USER / RA2_PASSWORD", EXIT_COMM)
        return NetconfDevice(self.netconf_host or self.host, self.netconf_port, self.user, self.password,
                             timeout=self.timeout, hostkey_verify=False)

    def masked(self) -> dict[str, Any]:
        return {"host": self.host, "user": self.user or None, "password": "********" if self.password else None,
                "netconf_port": self.netconf_port, "timeout": self.timeout, "source": self.source,
                "python": sys.executable, "netconf_library": NETCONF_SRC.is_dir() and _has_netconf_stack(),
                "docs": str(DOCS_DIR) if DOCS_DIR.is_dir() else None}


def _has_netconf_stack() -> bool:
    try:
        import libyang  # noqa: F401,PLC0415
        import ncclient  # noqa: F401,PLC0415
    except ImportError:
        return False
    return True


def resolve_conn(args: argparse.Namespace) -> Conn:
    env: dict[str, str] = {}
    source = "flags/environment"
    f = find_env_file()
    if f:
        env.update(load_env_file(f))
        source = f"{f} (+ environment overrides)"
    env.update({k: v for k, v in os.environ.items() if k.startswith("RA2_")})

    def pick(flag: Any, key: str, default: Any = None) -> Any:
        return flag if flag is not None else env.get(key, default)

    host = pick(args.host, "RA2_HOST") or None
    netconf_host = host                      # NetconfDevice has a port argument, so it wants the bare address
    api_port = str(pick(None, "RA2_API_PORT", "443") or "443")
    if host and api_port != "443" and ":" not in host.rsplit("]", 1)[-1]:
        host = f"{host}:{api_port}"          # rr_ra2_mgmt has no port parameter; same convention as `ra2`
    return Conn(host=host, user=pick(args.user, "RA2_USER", "") or "",
                password=pick(args.password, "RA2_PASSWORD", "") or "",
                netconf_port=int(pick(args.netconf_port, "RA2_NETCONF_PORT", 830)),
                timeout=int(pick(args.timeout, "RA2_TIMEOUT", 15)), source=source,
                netconf_host=netconf_host)


def run_guarded(fn: Any) -> Any:
    try:
        return fn()
    except Ra2RpcError as exc:
        info("scripting: device returned an error")
        dump({k: v for k, v in {"error": exc.code, "message_id": exc.message_id, "message_params": exc.message_params,
                                "suberrors": to_jsonable(exc.suberrors)}.items() if v is not None})
        sys.exit(EXIT_RPC)
    except Ra2LoginError as exc:
        die(f"login failed: {exc}", EXIT_COMM)
    except Ra2CommunicationError as exc:
        die(f"cannot reach device: {exc}", EXIT_COMM)
    except Ra2MgmtError as exc:
        die(f"{type(exc).__name__}: {exc}", EXIT_RPC)
    except KeyboardInterrupt:
        info("interrupted")
        sys.exit(130)


# --------------------------------------------------------------------------- templates


def render_template(kind: str, name: str, use_netconf: bool) -> str:
    tpl = TEMPLATES_DIR / f"{kind}{'_netconf' if use_netconf else ''}.py"
    if not tpl.is_file():
        die(f"template not found: {tpl}")
    return tpl.read_text().replace("__SCRIPT_NAME__", name)


def cmd_new(args: argparse.Namespace, conn: Conn) -> None:
    name = args.name if args.name.endswith(".py") else args.name + ".py"
    text = render_template(args.kind, name, args.netconf)
    if args.out == "-":
        sys.stdout.write(text)
        return
    out = Path(args.out or name)
    if out.exists() and not args.force:
        die(f"{out} exists (use --force to overwrite)")
    out.write_text(text)
    info(f"wrote {out} ({args.kind}{', netconf' if args.netconf else ''}) — next: scripting check {out}")


# --------------------------------------------------------------------------- static checks


@dataclasses.dataclass
class Finding:
    severity: str  # error | warning | info
    rule: str
    line: int
    message: str


class Checker(ast.NodeVisitor):
    """Walks one script and collects findings against the Scripting runtime contract."""

    def __init__(self, source: str, allowed_modules: set[str] | None, firmware: str | None) -> None:
        self.src = source
        self.lines = source.splitlines()
        self.allowed = allowed_modules
        self.firmware = firmware
        self.findings: list[Finding] = []
        self.imports: dict[str, int] = {}
        self.stdin_first_use: int | None = None
        self.payload_read: int | None = None
        self.payload_name: str | None = None
        self.reconfigure = False
        self.prints_without_flush: list[int] = []
        self.has_loop = False
        self.loop_lines: list[int] = []
        self.sigterm = False
        self.rlimit_cpu = False
        self.long_sleeps: list[tuple[int, float]] = []
        self.argv_used = False
        self.argparse_used = False
        self.sys_exit = False
        self.opens: list[tuple[int, str]] = []
        self.workdir_var: str | None = None
        self.in_loop = 0

    def add(self, severity: str, rule: str, node: ast.AST | int, message: str) -> None:
        line = node if isinstance(node, int) else getattr(node, "lineno", 0)
        self.findings.append(Finding(severity, rule, line, message))

    # --- imports
    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self.imports.setdefault(a.name.split(".")[0], node.lineno)
            if a.name == "argparse":
                self.argparse_used = True
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level and node.level > 0:
            self.add("error", "single-file", node, "relative import: a script is one file, there is no package")
        elif node.module:
            self.imports.setdefault(node.module.split(".")[0], node.lineno)
        self.generic_visit(node)

    # --- calls
    def visit_Call(self, node: ast.Call) -> None:
        name = _dotted(node.func)
        src = ast.get_source_segment(self.src, node) or ""
        if "sys.stdin" in src and self.stdin_first_use is None:
            self.stdin_first_use = node.lineno
        if name in ("json.load",) and node.args and _dotted(node.args[0]) == "sys.stdin":
            if self.payload_read is None:
                self.payload_read = node.lineno
        if name in ("sys.stdout.reconfigure", "sys.stderr.reconfigure"):
            if any(k.arg == "line_buffering" for k in node.keywords):
                self.reconfigure = True
        if name == "print":
            if not any(k.arg == "flush" for k in node.keywords):
                self.prints_without_flush.append(node.lineno)
            if self.payload_name:
                if any(_dotted(a) == self.payload_name for a in node.args):
                    self.add("error", "secrets", node, f"prints the whole launcher payload ({self.payload_name}); it holds the session token and private key")
                elif any(self.payload_name in (ast.get_source_segment(self.src, a) or "") for a in node.args):
                    self.add("warning", "secrets", node, f"prints a field of the launcher payload; make sure it is never ra2_api_token, private_key or netconf_server_hostkey")
        if name in ("json.dump", "json.dumps") and node.args and self.payload_name and _dotted(node.args[0]) == self.payload_name:
            self.add("error", "secrets", node, "serialises the launcher payload — never write or print it")
        if name == "signal.signal" and node.args and "SIGTERM" in (ast.get_source_segment(self.src, node.args[0]) or ""):
            self.sigterm = True
        if name == "resource.setrlimit" and node.args and "RLIMIT_CPU" in (ast.get_source_segment(self.src, node.args[0]) or ""):
            self.rlimit_cpu = True
        if name == "time.sleep" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, (int, float)):
            if node.args[0].value > 5 and self.in_loop:
                self.long_sleeps.append((node.lineno, float(node.args[0].value)))
        if name in ("sys.exit",):
            self.sys_exit = True
        if name == "open" and node.args:
            self.opens.append((node.lineno, ast.get_source_segment(self.src, node.args[0]) or ""))
        if name in ("Device", "rr_ra2_mgmt.device.Device"):
            self._check_device_call(node, src)
        if name in ("NetconfDevice", "rr_netconf_mgmt.device.NetconfDevice"):
            self._check_netconf_call(node, src)
        self.generic_visit(node)

    def _check_device_call(self, node: ast.Call, src: str) -> None:
        kw = {k.arg: k.value for k in node.keywords}
        positional = len(node.args)
        literal_pw = ("password" in kw and isinstance(kw["password"], ast.Constant) and kw["password"].value) or (
            positional >= 3 and isinstance(node.args[2], ast.Constant) and node.args[2].value)
        if literal_pw:
            self.add("error", "secrets", node, "Device(...) with a literal password — inside the device use token=payload['ra2_api_token']")
        if "token" not in kw and (positional >= 2 or "user" in kw or "password" in kw):
            self.add("warning", "in-device-auth", node, "Device(...) logs in with user/password; inside the device prefer Device(host='localhost', token=payload['ra2_api_token'])")
        host = kw.get("host") if "host" in kw else (node.args[0] if node.args else None)
        if isinstance(host, ast.Constant) and host.value not in ("localhost", "127.0.0.1") and "intermediate" not in kw:
            self.add("info", "in-device-host", node, f"Device host is {host.value!r}; the local device is 'localhost', a remote unit is host=<ip> with intermediate='localhost'")

    def _check_netconf_call(self, node: ast.Call, src: str) -> None:
        kw = {k.arg: k.value for k in node.keywords}
        if "password" in kw and isinstance(kw["password"], ast.Constant) and kw["password"].value:
            self.add("error", "secrets", node, "NetconfDevice(...) with a literal password — inside the device use key_base64=payload['private_key'].encode()")
        if "hostkey_verify" in kw and isinstance(kw["hostkey_verify"], ast.Constant) and kw["hostkey_verify"].value is False:
            self.add("warning", "in-device-auth", node, "hostkey_verify=False — inside the device pass hostkey_b64=payload['netconf_server_hostkey'] instead")
        if "key_base64" in kw:
            seg = ast.get_source_segment(self.src, kw["key_base64"]) or ""
            if ".encode(" not in seg and "b'" not in seg and 'b"' not in seg:
                self.add("warning", "in-device-auth", node, "key_base64 expects bytes: payload['private_key'].encode()")
        if "port" not in kw:
            self.add("warning", "in-device-auth", node, "NetconfDevice without port= — inside the device use port=payload['netconf_server_port']")
        if "user" not in kw:
            self.add("warning", "in-device-auth", node, "NetconfDevice without user= — inside the device use user=payload['user']")

    # --- assignments / loops / misc
    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Call) and _dotted(node.value.func) == "json.load" and node.value.args \
                and _dotted(node.value.args[0]) == "sys.stdin" and node.targets and isinstance(node.targets[0], ast.Name):
            self.payload_name = node.targets[0].id
        seg = ast.get_source_segment(self.src, node.value) or ""
        if "user_workdir" in seg and node.targets and isinstance(node.targets[0], ast.Name):
            self.workdir_var = node.targets[0].id
        for t in node.targets:
            if isinstance(t, ast.Name) and re.search(r"passw|secret|token", t.id, re.I) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str) and node.value.value:
                self.add("error", "secrets", node, f"literal secret assigned to {t.id}; credentials come from the stdin payload")
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.has_loop = True
        self.loop_lines.append(node.lineno)
        self.in_loop += 1
        self.generic_visit(node)
        self.in_loop -= 1

    def visit_For(self, node: ast.For) -> None:
        self.in_loop += 1
        self.generic_visit(node)
        self.in_loop -= 1

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if _dotted(node) == "sys.argv":
            self.argv_used = True
        self.generic_visit(node)

    # --- final rules
    def finish(self) -> list[Finding]:
        f = self.findings
        if self.payload_read is None:
            sev = "warning" if not (self.argv_used or self.argparse_used or "rr_" in "".join(self.imports)) else "error"
            f.append(Finding(sev, "payload", 1, "does not read the launcher payload (payload = json.load(sys.stdin)) — needed for tokens/keys/workdir; harmless only for trivial scripts"))
        elif self.stdin_first_use is not None and self.stdin_first_use < self.payload_read:
            f.append(Finding("error", "payload", self.stdin_first_use, "sys.stdin is used before json.load(sys.stdin); the payload must be read first"))
        if self.payload_read is not None and self.argparse_used:
            pa = self._first_line_matching(r"parse_args\(")
            if pa and pa < self.payload_read:
                f.append(Finding("warning", "payload", pa, "parse_args() runs before the payload is read; read stdin first (argparse may exit and leave the pipe unread)"))
        if not self.reconfigure and self.prints_without_flush:
            f.append(Finding("warning", "buffering", self.prints_without_flush[0],
                             f"{len(self.prints_without_flush)} print() without flush=True and no sys.stdout.reconfigure(line_buffering=True): output of a killed script is lost"))
        long_running = self.has_loop and ("time.sleep" in self.src or "while True" in self.src or "while not" in self.src)
        if long_running:
            if not self.sigterm:
                f.append(Finding("error", "sigterm", self.loop_lines[0], "long-running loop without a SIGTERM handler; a stop will end in SIGKILL and state 'killed'"))
            if not self.rlimit_cpu:
                f.append(Finding("error", "rlimit-cpu", self.loop_lines[0], "long-running loop without resource.setrlimit(RLIMIT_CPU, ...); the 20 s CPU soft limit kills it (SIGXCPU)"))
            for line, secs in self.long_sleeps:
                f.append(Finding("warning", "sigterm", line, f"time.sleep({secs:g}) inside the loop delays the SIGTERM handler; sleep in 1 s steps and test the stop flag"))
        if self.argv_used and not self.argparse_used:
            f.append(Finding("info", "arguments", 1, "reads sys.argv directly; argparse gives instances parametrisation with validation"))
        for line, target in self.opens:
            if self.workdir_var and self.workdir_var in target:
                continue
            if "user_workdir" in target:
                continue
            if target.startswith(("'", '"')) or "/" in target:
                f.append(Finding("warning", "filesystem", line, f"open({target}) outside payload['user_workdir']; AppArmor denies most paths, files must live in the working directory"))
        self._check_imports()
        f.sort(key=lambda x: ({"error": 0, "warning": 1, "info": 2}[x.severity], x.line))
        return f

    def _first_line_matching(self, pattern: str) -> int | None:
        for i, line in enumerate(self.lines, 1):
            if re.search(pattern, line):
                return i
        return None

    def _check_imports(self) -> None:
        host_stdlib = set(getattr(sys, "stdlib_module_names", set()))
        for mod, line in sorted(self.imports.items(), key=lambda kv: kv[1]):
            if self.allowed is not None:
                if mod not in self.allowed:
                    self.findings.append(Finding("error", "imports", line,
                                                 f"module {mod!r} is not importable on firmware {self.firmware} (list: scripting modules)"))
            elif mod not in host_stdlib and mod not in KNOWN_THIRD_PARTY:
                self.findings.append(Finding("warning", "imports", line,
                                             f"module {mod!r} is neither stdlib nor a known device package; verify with `scripting modules --fetch`"))
            elif mod in host_stdlib and mod in {"tkinter", "curses", "multiprocessing", "ctypes", "venv", "ensurepip", "idlelib", "turtle"}:
                self.findings.append(Finding("warning", "imports", line, f"module {mod!r} is unlikely to exist in the reduced device stdlib"))


def _dotted(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _version_key(path: Path) -> tuple[int, ...]:
    """Sort module-list files by firmware version, not by filename.

    Lexicographically "modules-2.3.10.0.json" sorts before "modules-2.3.6.0.json", so a plain
    sorted()[-1] picks an older firmware's list as the newest.
    """
    stem = path.stem
    version = stem.split("-", 1)[1] if "-" in stem else stem
    parts: list[int] = []
    for chunk in version.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def load_allowed_modules(firmware: str | None, strict: bool = False) -> tuple[set[str] | None, str | None]:
    """Return (allowed module names, firmware) from the newest cached module list, or (None, None).

    With strict=True a named firmware that has no cached list yields (None, None) instead of
    falling back to some other firmware's list — a deploy gate must not answer from the wrong
    device's module set.
    """
    if not MODULES_DIR.is_dir():
        return None, None
    files = sorted(MODULES_DIR.glob("*.json"), key=_version_key)
    if firmware:
        matching = [f for f in files if firmware in f.name]
        if not matching and strict:
            return None, None
        files = matching or files
    elif strict:
        return None, None
    if not files:
        return None, None
    data = json.loads(files[-1].read_text())
    if data.get("partial"):
        return None, None
    return set(data.get("stdlib", [])) | set(data.get("third_party", [])), data.get("firmware")


def probe_firmware(conn: Conn) -> str:
    """Firmware version of the deploy target, so its imports are checked against the right list."""
    try:
        dev = conn.ra2()
        with dev:
            return str(dev.device_info_get_private().result.firmware_version)
    except Ra2MgmtError as exc:
        die(f"cannot read the target firmware version ({type(exc).__name__}: {exc}); "
            f"pass --firmware VERSION to check against a cached list, or --skip-check", EXIT_COMM)


def check_file(path: Path, allowed: set[str] | None, firmware: str | None) -> list[Finding]:
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        return [Finding("error", "syntax", exc.lineno or 0, f"syntax error: {exc.msg}")]
    if len(src.encode()) > 512 * 1024:
        return [Finding("error", "size", 1, "script larger than 512 KiB; it is stored inside the device configuration")]
    if "RA2_HOST" in src or "RA2_PASSWORD" in src:
        return [Finding("info", "local-tool", 1, "reads RA2_* environment variables: this is a local tool, not a script — runtime-contract checks skipped")]
    checker = Checker(src, allowed, firmware)
    checker.visit(tree)
    return checker.finish()


def cmd_check(args: argparse.Namespace, conn: Conn) -> None:
    allowed, fw = load_allowed_modules(args.firmware)
    if allowed is None:
        info("scripting: no complete module list cached — import checks are heuristic (run `scripting modules --fetch` on the target)")
    worst = 0
    report: dict[str, Any] = {}
    for f in args.files:
        p = Path(f)
        if not p.is_file():
            die(f"file not found: {p}")
        findings = check_file(p, allowed, fw)
        report[str(p)] = [dataclasses.asdict(x) for x in findings]
        errors = sum(x.severity == "error" for x in findings)
        warnings = sum(x.severity == "warning" for x in findings)
        worst = max(worst, 2 if errors else 1 if warnings else 0)
        if not args.json:
            sys.stdout.write(f"{p}: {errors} error(s), {warnings} warning(s)\n")
            for x in findings:
                sys.stdout.write(f"  {x.severity:7} L{x.line:<4} [{x.rule}] {x.message}\n")
    if args.json:
        dump(report, args.compact)
    sys.exit(EXIT_CHECK if worst == 2 else 0)


# --------------------------------------------------------------------------- RA2 API side


def latest_execution(dev: Device, script: str, instance: str | None) -> Any:
    """The newest execution record.

    The device returns executions oldest-first, so asking for one record yields the *first* run a
    script ever had, not the current one. Page to the end with id_exclusive and keep the highest
    id — otherwise `scripting logs`, `scripting run` and `scripting modules --fetch` all report a stale execution.
    """
    inst: dict[str, Any] = {"script_name": script}
    if instance:
        inst["instance_names"] = [instance]
    newest: Any = None
    last_id: int | None = None
    for _ in range(100):                      # bounded, so an odd reply cannot spin here forever
        pag: dict[str, Any] = {"limit": PAGE}
        if last_id is not None:
            pag["id_exclusive"] = last_id
        rows = dev.script_execution_get(pag, filter={"instances": [inst], "include_outdated": True}).result.executions
        if not rows:
            break
        for row in rows:
            if newest is None or row.id > newest.id:
                newest = row
        last_id = max(row.id for row in rows)
        if len(rows) < PAGE:
            break
    return newest


def iter_output(dev: Device, execution_id: int, after_id: int | None = None) -> Any:
    last = after_id
    while True:
        pag: dict[str, Any] = {"limit": PAGE}
        if last is not None:
            pag["id_exclusive"] = last
        res = dev.script_execution_output_get({"execution_id": execution_id}, pag).result
        lines = res.lines or []
        yield from lines
        if len(lines) < PAGE:
            return
        last = lines[-1].id


def print_execution(ex: Any) -> None:
    stop = ex.stop_trigger.type if ex.stop_trigger else None
    sys.stdout.write(f"execution {ex.id}: {ex.script_name}/{ex.instance_name} state={ex.state} exit_code={ex.exit_code} "
                     f"duration={ex.duration} start={ex.start_trigger.type} stop={stop} args={ex.arguments!r} "
                     f"outdated={ex.is_outdated}\n")


def cmd_list(args: argparse.Namespace, conn: Conn) -> None:
    dev = conn.ra2()

    def go() -> None:
        with dev:
            ov = dev.script_overview_get().result
        if args.json:
            dump(ov, args.compact)
            return
        if not ov.scripts:
            sys.stdout.write("no scripts deployed\n")
        for s in ov.scripts:
            sys.stdout.write(f"{s.name}\n")
            for i in s.instances:
                le = i.last_execution
                last = (f"last #{le.id} {le.state} exit={getattr(le, 'exit_code', None)} trig={le.start_trigger.type}"
                        f"{' OUTDATED' if le.is_outdated else ''}") if le else "never run"
                sys.stdout.write(f"  {i.name:20} {last}\n")

    run_guarded(go)


def cmd_start_stop(args: argparse.Namespace, conn: Conn) -> None:
    dev = conn.ra2()
    method = args.cmd

    def go() -> None:
        with dev:
            res = getattr(dev, f"script_{method}")(args.script, args.instance).result
        for s in res.scripts:
            if s.name == args.script:
                for i in s.instances:
                    if i.name == args.instance:
                        dump({"script": s.name, "instance": i.name, "last_execution": to_jsonable(i.last_execution)}, args.compact)
                        return
        dump(res, args.compact)

    run_guarded(go)


def cmd_run(args: argparse.Namespace, conn: Conn) -> None:
    dev = conn.ra2()

    def go() -> None:
        with dev:
            previous = latest_execution(dev, args.script, args.instance)
            if not args.no_start:
                dev.script_start(args.script, args.instance)
                info(f"scripting: started {args.script}/{args.instance}")
            deadline = time.monotonic() + args.wait
            ex = None
            printed_after: int | None = None
            while True:
                ex = latest_execution(dev, args.script, args.instance)
                is_new = ex is not None and (previous is None or ex.id != previous.id or args.no_start)
                if is_new and args.follow:
                    for line in iter_output(dev, ex.id, printed_after):
                        sys.stdout.write(f"{line.content}\n")
                        printed_after = line.id
                    sys.stdout.flush()
                if is_new and (ex.state in FINAL_STATES or (args.no_start and not args.follow)):
                    break
                if time.monotonic() > deadline:
                    info(f"scripting: still running after {args.wait} s; showing output so far (use `scripting logs --follow`)")
                    break
                time.sleep(2)
            if ex is None:
                die("no execution record found", EXIT_RPC)
            print_execution(ex)
            if not args.follow:
                sys.stdout.write("--- output ---\n")
                for line in iter_output(dev, ex.id):
                    sys.stdout.write(f"{line.content}\n")
            sys.exit(0 if ex.exit_code in (0, None) and ex.state != "killed" else 1)

    run_guarded(go)


def cmd_logs(args: argparse.Namespace, conn: Conn) -> None:
    dev = conn.ra2()

    def go() -> None:
        with dev:
            if args.execution is not None:
                ex_id = args.execution
                ex = None
            else:
                ex = latest_execution(dev, args.script, args.instance)
                if ex is None:
                    die("no execution found", EXIT_RPC)
                ex_id = ex.id
                print_execution(ex)
            last: int | None = None
            while True:
                for line in iter_output(dev, ex_id, last):
                    sys.stdout.write(f"{line.id:6d} | {line.content}\n")
                    last = line.id
                sys.stdout.flush()
                if not args.follow:
                    return
                ex = latest_execution(dev, args.script, args.instance)
                if ex is None or ex.id != ex_id or ex.state in FINAL_STATES:
                    if ex is not None and ex.id == ex_id:
                        print_execution(ex)
                    return
                time.sleep(2)

    run_guarded(go)


def cmd_executions(args: argparse.Namespace, conn: Conn) -> None:
    dev = conn.ra2()

    def go() -> None:
        inst: dict[str, Any] = {"script_name": args.script}
        if args.instance:
            inst["instance_names"] = [args.instance]
        with dev:
            res = dev.script_execution_get({"limit": args.limit}, filter={"instances": [inst], "include_outdated": True}).result
        if args.json:
            dump(res, args.compact)
            return
        for ex in res.executions:
            print_execution(ex)
        if not res.executions:
            sys.stdout.write("no executions\n")

    run_guarded(go)


def _is_on(value: Any) -> bool:
    """True for either representation of an Off/On leaf: int 0/1 or the string Off/On."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() in {"on", "true", "1", "enabled"}


def cmd_enable(args: argparse.Namespace, conn: Conn) -> None:
    dev = conn.ra2()
    section, key = ENABLE_KEY

    def go() -> None:
        with dev:
            cfg = dev.settings_get().result.config_data
            current = cfg.get(section, {}).get(key)
            if args.on is None:
                dump({f"{section}.{key}": current, "enabled": _is_on(current)}, args.compact)
                return
            # The RA2 API renders this leaf as int 0/1 while YANG calls it Off/On. Write it back
            # in whatever shape it arrived in, or the device rejects the save and the flag silently
            # stays as it was.
            wanted: Any = (1 if args.on else 0) if isinstance(current, int) else ("On" if args.on else "Off")
            if _is_on(current) == bool(args.on):
                dump({f"{section}.{key}": current, "enabled": _is_on(current), "changed": False}, args.compact)
                return
            cfg[section][key] = wanted
            info(f"scripting: saving {section}.{key}={wanted!r} (settings_save_init + reconnect; restarts the Scripting subsystem)")
            saved = dev.helper.settings_save(cfg).result
            now = saved.config_data[section][key]
            if _is_on(now) != bool(args.on):
                die(f"{section}.{key} is still {now!r} after the save; the device rejected the change", EXIT_RPC)
            dump({f"{section}.{key}": now, "enabled": _is_on(now), "changed": True, "previous": current}, args.compact)

    run_guarded(go)


# --------------------------------------------------------------------------- NETCONF side (deploy)


def launcher_fragment(script_name: str, source: str | None, instance: str | None, extra: dict[str, str],
                 delete: bool = False, role: str | None = None) -> str:
    ET.register_namespace("", LAUNCHER_NS)
    ET.register_namespace("nc", NC_NS)
    root = ET.Element(f"{{{LAUNCHER_NS}}}sdk-launcher")
    scripts = ET.SubElement(root, f"{{{LAUNCHER_NS}}}scripts")
    if delete:
        scripts.set(f"{{{NC_NS}}}operation", "delete")
    ET.SubElement(scripts, f"{{{LAUNCHER_NS}}}name").text = script_name
    if source is not None:
        ET.SubElement(scripts, f"{{{LAUNCHER_NS}}}source").text = source
    if role is not None:
        # mandatory in rr-sdk-launcher; without it the device answers "An expected element is missing"
        ET.SubElement(scripts, f"{{{LAUNCHER_NS}}}execution-role").text = role
    if instance:
        inst = ET.SubElement(scripts, f"{{{LAUNCHER_NS}}}instances")
        ET.SubElement(inst, f"{{{LAUNCHER_NS}}}name").text = instance
        for leaf, value in extra.items():
            node = inst
            for seg in leaf.split("/"):
                node = ET.SubElement(node, f"{{{LAUNCHER_NS}}}{seg}")
            node.text = value
    return ET.tostring(root, encoding="unicode")


def cmd_deploy(args: argparse.Namespace, conn: Conn) -> None:
    p = Path(args.file)
    if not p.is_file():
        die(f"file not found: {p}")
    if not args.skip_check:
        # Check against the module list of the device being deployed to, not whichever list
        # happens to be cached: a script importing a library this firmware does not ship must
        # fail here rather than at the first run on the device.
        fw = args.firmware or probe_firmware(conn)
        allowed, _ = load_allowed_modules(fw, strict=True)
        if allowed is None:
            die(f"no complete module list cached for firmware {fw}: run `scripting modules --fetch` "
                f"against this device first, or deploy with --firmware VERSION / --skip-check", EXIT_CHECK)
        findings = check_file(p, allowed, fw)
        errors = [x for x in findings if x.severity == "error"]
        if errors:
            for x in errors:
                info(f"  error L{x.line} [{x.rule}] {x.message}")
            die(f"{len(errors)} check error(s) against firmware {fw}; fix them or pass --skip-check", EXIT_CHECK)
    extra: dict[str, str] = {}
    for item in args.set or []:
        if "=" not in item:
            die(f"--set expects LEAF=VALUE, got {item!r}")
        k, _, v = item.partition("=")
        extra[k.strip()] = v
    name = args.name or p.name
    source = p.read_text(encoding="utf-8")
    fragment = launcher_fragment(name, source, args.instance, extra, role=args.role)
    if args.dry_run:
        sys.stdout.write(fragment + "\n")
        return
    from rr_netconf_mgmt.config import NetconfConfig  # noqa: PLC0415
    from rr_netconf_mgmt.exceptions import NetconfMgmtError  # noqa: PLC0415
    dev = conn.netconf()
    info(f"scripting: edit-config merge on rr-sdk-launcher: script {name!r}" + (f", instance {args.instance!r}" if args.instance else ""))
    try:
        with dev:
            dev.set_config(NetconfConfig(fragment))
    except NetconfMgmtError as exc:
        die(f"deploy failed ({type(exc).__name__}): {exc}", EXIT_RPC)
    dump({"deployed": name, "role": args.role, "instance": args.instance, "bytes": len(source), "extra": extra,
          "next": f"scripting run {name} {args.instance}" if args.instance else f"scripting deploy ... --instance NAME"}, args.compact)


def cmd_undeploy(args: argparse.Namespace, conn: Conn) -> None:
    fragment = launcher_fragment(args.script, None, None, {}, delete=True)
    if args.dry_run:
        sys.stdout.write(fragment + "\n")
        return
    from rr_netconf_mgmt.config import NetconfConfig  # noqa: PLC0415
    from rr_netconf_mgmt.exceptions import NetconfMgmtError  # noqa: PLC0415
    dev = conn.netconf()
    info(f"scripting: edit-config delete of script {args.script!r} and all its instances")
    try:
        with dev:
            dev.set_config(NetconfConfig(fragment))
    except NetconfMgmtError as exc:
        die(f"undeploy failed ({type(exc).__name__}): {exc}", EXIT_RPC)
    dump({"deleted": args.script}, args.compact)


# --------------------------------------------------------------------------- module inventory


def parse_module_listing(lines: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"python": None, "stdlib": [], "third_party": []}
    section = None
    for raw in lines:
        line = raw.strip()
        if line.startswith("Python "):
            out["python"] = line.split()[1]
        elif line.startswith("Stdlib modules"):
            section = "stdlib"
        elif line.startswith("3rd party modules"):
            section = "third_party"
        elif section and line:
            out[section].extend(m.strip() for m in line.split(",") if m.strip())
    return out


def cmd_modules(args: argparse.Namespace, conn: Conn) -> None:
    MODULES_DIR.mkdir(parents=True, exist_ok=True)
    if not args.fetch:
        files = sorted(MODULES_DIR.glob("*.json"), key=_version_key)
        if not files:
            die("no cached module list; run `scripting modules --fetch` against a device")
        data = json.loads(files[-1].read_text())
        if args.json:
            dump(data, args.compact)
        else:
            sys.stdout.write(f"firmware {data.get('firmware')} python {data.get('python')}"
                             f"{' (PARTIAL list)' if data.get('partial') else ''} — {files[-1].name}\n")
            sys.stdout.write(f"stdlib ({len(data.get('stdlib', []))}): {', '.join(data.get('stdlib', []))}\n")
            sys.stdout.write(f"third party ({len(data.get('third_party', []))}): {', '.join(data.get('third_party', []))}\n")
        return
    script_src = (DOCS_DIR / "examples" / "list_modules.py")
    if not script_src.is_file():
        script_src = TEMPLATES_DIR / "list_modules.py"
    if not script_src.is_file():
        die("list_modules.py not found (docs/scripting/examples or templates)")
    instance = args.instance
    from rr_netconf_mgmt.config import NetconfConfig  # noqa: PLC0415
    from rr_netconf_mgmt.exceptions import NetconfMgmtError  # noqa: PLC0415
    ncdev = conn.netconf()
    info(f"scripting: deploying {LIST_MODULES_SCRIPT}/{instance} (edit-config merge)")
    try:
        with ncdev:
            ncdev.set_config(NetconfConfig(launcher_fragment(LIST_MODULES_SCRIPT, script_src.read_text(), instance, {},
                                                        role="admin")))
    except NetconfMgmtError as exc:
        die(f"deploy failed ({type(exc).__name__}): {exc}", EXIT_RPC)
    dev = conn.ra2()

    def go() -> None:
        with dev:
            fw = dev.device_info_get_private().result.firmware_version
            previous = latest_execution(dev, LIST_MODULES_SCRIPT, instance)
            dev.script_start(LIST_MODULES_SCRIPT, instance)
            deadline = time.monotonic() + 120
            ex = None
            while time.monotonic() < deadline:
                ex = latest_execution(dev, LIST_MODULES_SCRIPT, instance)
                if ex and (previous is None or ex.id != previous.id) and ex.state in FINAL_STATES:
                    break
                time.sleep(2)
            if ex is None or ex.state not in FINAL_STATES:
                die("list_modules did not finish in time", EXIT_RPC)
            lines = [l.content for l in iter_output(dev, ex.id)]
        data = parse_module_listing(lines)
        data["firmware"] = fw
        data["fetched"] = time.strftime("%Y-%m-%d")
        data["partial"] = False
        out = MODULES_DIR / f"modules-{fw}.json"
        out.write_text(json.dumps(data, indent=2) + "\n")
        info(f"scripting: cached {len(data['stdlib'])} stdlib + {len(data['third_party'])} third-party modules -> {out}")
        if not args.keep:
            info(f"scripting: remove the helper script with `scripting undeploy {LIST_MODULES_SCRIPT}` (kept for reuse)")
        dump({"firmware": fw, "python": data["python"], "stdlib": len(data["stdlib"]),
              "third_party": data["third_party"], "file": str(out)}, args.compact)

    run_guarded(go)


def cmd_env(args: argparse.Namespace, conn: Conn) -> None:
    dump(conn.masked(), args.compact)


# --------------------------------------------------------------------------- argument parsing

GLOBAL_DEFAULTS: dict[str, Any] = {"host": None, "user": None, "password": None, "netconf_port": None,
                                   "timeout": None, "compact": False, "json": False}


def _sub_parser_class(common: argparse.ArgumentParser) -> type[argparse.ArgumentParser]:
    class _Sub(argparse.ArgumentParser):
        def __init__(self, **kwargs: Any) -> None:
            kwargs.setdefault("parents", [common])
            super().__init__(**kwargs)

    return _Sub


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    g = common.add_argument_group("connection (override RA2_* env / .env.ra2)")
    g.add_argument("--host")
    g.add_argument("--user")
    g.add_argument("--password")
    g.add_argument("--netconf-port", dest="netconf_port", type=int)
    g.add_argument("--timeout", type=int)
    o = common.add_argument_group("output")
    o.add_argument("--compact", "-c", action="store_true")
    o.add_argument("--json", action="store_true", help="machine-readable output where a table is the default")

    p = argparse.ArgumentParser(parents=[common], prog="scripting", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND", parser_class=_sub_parser_class(common))

    sub.add_parser("env", help="resolved connection settings").set_defaults(func=cmd_env)

    a = sub.add_parser("new", help="generate a script skeleton that follows the runtime contract")
    a.add_argument("name", help="script name, e.g. status_monitor.py")
    a.add_argument("--kind", choices=("continuous", "oneshot"), default="oneshot")
    a.add_argument("--netconf", action="store_true", help="NETCONF (per-run key) instead of the RA2 API token")
    a.add_argument("--out", help="output path (default: NAME in the current directory, '-' for stdout)")
    a.add_argument("--force", action="store_true")
    a.set_defaults(func=cmd_new)

    a = sub.add_parser("check", help="static checks: payload, buffering, SIGTERM, RLIMIT_CPU, secrets, imports, files")
    a.add_argument("files", nargs="+")
    a.add_argument("--firmware", help="use the cached module list of this firmware version")
    a.set_defaults(func=cmd_check)

    a = sub.add_parser("modules", help="modules importable on the device (cached); --fetch runs list_modules.py on the device")
    a.add_argument("--fetch", action="store_true")
    a.add_argument("--instance", default="probe")
    a.add_argument("--keep", action="store_true", help="do not suggest removing the helper script")
    a.set_defaults(func=cmd_modules)

    a = sub.add_parser("enable", help="show main.RR_Sdk_Enable; --on/--off change it (RA2 API settings_save)")
    a.add_argument("--on", dest="on", action="store_true", default=None)
    a.add_argument("--off", dest="on", action="store_false")
    a.set_defaults(func=cmd_enable)

    a = sub.add_parser("deploy", help="store FILE as a script (+ optional instance) via NETCONF edit-config merge")
    a.add_argument("file")
    a.add_argument("--name", help="script name in the device (default: file name)")
    a.add_argument("--instance", help="instance name to create/update")
    a.add_argument("--set", action="append", metavar="LEAF=VALUE",
                   help="extra instance leaf (name from `netconf tree /rr-sdk-launcher:sdk-launcher`), e.g. arguments='--interval 30'; nested as a/b/c")
    a.add_argument("--role", default="admin", choices=["admin", "sectech", "tech", "guest"],
                   help="execution-role of the script: the permission level its runs get (default admin)")
    a.add_argument("--firmware", help="check imports against this cached firmware's module list "
                                      "instead of asking the target device")
    a.add_argument("--skip-check", action="store_true")
    a.add_argument("--dry-run", action="store_true", help="print the XML fragment instead of sending it")
    a.set_defaults(func=cmd_deploy)

    a = sub.add_parser("undeploy", help="delete a script and its instances via NETCONF edit-config")
    a.add_argument("script")
    a.add_argument("--dry-run", action="store_true")
    a.set_defaults(func=cmd_undeploy)

    sub.add_parser("list", help="scripts, instances and last executions").set_defaults(func=cmd_list)

    for verb in ("start", "stop"):
        a = sub.add_parser(verb, help=f"manual trigger: script_{verb}")
        a.add_argument("script")
        a.add_argument("instance")
        a.set_defaults(func=cmd_start_stop)

    a = sub.add_parser("run", help="start an instance, wait for it to end, print the execution record and output")
    a.add_argument("script")
    a.add_argument("instance")
    a.add_argument("--wait", type=int, default=120, help="seconds to wait for the run to end (default 120)")
    a.add_argument("--no-start", action="store_true", help="do not start; report the latest execution")
    a.add_argument("--follow", action="store_true", help="stream output while waiting")
    a.set_defaults(func=cmd_run)

    a = sub.add_parser("logs", help="captured output of the latest (or a given) execution")
    a.add_argument("script")
    a.add_argument("instance")
    a.add_argument("--execution", type=int, metavar="ID")
    a.add_argument("--follow", action="store_true", help="keep polling while the execution runs")
    a.set_defaults(func=cmd_logs)

    a = sub.add_parser("executions", help="execution history of a script (optionally one instance)")
    a.add_argument("script")
    a.add_argument("instance", nargs="?")
    a.add_argument("--limit", type=int, default=20)
    a.set_defaults(func=cmd_executions)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    for key, value in GLOBAL_DEFAULTS.items():
        if not hasattr(args, key):
            setattr(args, key, value)
    conn = resolve_conn(args)
    args.func(args, conn)


if __name__ == "__main__":
    main()
