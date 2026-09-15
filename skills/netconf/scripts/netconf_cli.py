#!/usr/bin/env python3
"""
netconf — command-line access to RipEx2 / RA2 devices over NETCONF (RFC 6241, SSH port 830).

Wraps the bundled `rr_netconf_mgmt` library (skills/netconf/rac_netconf_mgmt: ncclient + libyang).
Run through the `netconf` wrapper next to this file, which picks the repo .venv created by
scripts/setup.sh. JSON/XML on stdout, diagnostics on stderr.

    netconf env                                  resolved connection settings
    netconf ping [--all]                         open a session, show session id + capabilities
    netconf modules [PATTERN]                    YANG modules advertised by the device
    netconf schema MODULE [--revision R] [--out F]   raw YANG text (get-schema)
    netconf tree [MODULE | DATA-PATH] [--depth N]    schema tree (rw/ro, types)
    netconf find TEXT [--module M]               search schema nodes by name/description
    netconf get [XPATH] [--xml]                  get-config → JSON dict (libyang) or XML
    netconf leaf PATH                            one typed leaf value
    netconf set PATH=VALUE ...                   read parent subtree, set leaves, edit-config merge
    netconf edit FILE [--operation merge|replace]    edit-config from an XML fragment or JSON dict
    netconf state [XPATH] [--json]               <get> operational data (Diagnostics, status)
    netconf rpc XML|@FILE                        dispatch an arbitrary RPC, print the reply
    netconf methods [PATTERN] / doc NAME         library methods and docstrings

Paths use the libyang *data path* format: module prefix only at module boundaries,
e.g. /mbm-root:config_data/main/Main/RR_StationName. XPath filters use the same prefix style.
--offline makes tree/find work without a device, from the bundled mbm-root example model.

Connection: --host/--port/--user/--password/--timeout/--hostkey-verify flags, else RA2_HOST,
RA2_NETCONF_PORT, RA2_USER, RA2_PASSWORD, RA2_TIMEOUT, RA2_NETCONF_HOSTKEY_VERIFY, else a `.env.ra2`
file found by walking up from the current directory (shared with skills/api).
"""

from __future__ import annotations

import argparse
import dataclasses
import inspect
import json
import os
import re
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
LIB_SRC = SKILL_DIR / "rac_netconf_mgmt" / "src"
EXAMPLE_YANG = SKILL_DIR / "rac_netconf_mgmt" / "examples" / "mbm-root@2023-04-29.yang"
IETF_DIR = Path("/usr/local/share/yang/modules/libyang")
sys.path.insert(0, str(LIB_SRC))

try:
    import libyang
    import lxml.etree as etree
    from rr_netconf_mgmt.config import NetconfConfig
    from rr_netconf_mgmt.device import NetconfDevice
    from rr_netconf_mgmt.exceptions import (
        NetconfMgmtConfigError,
        NetconfMgmtConnectionError,
        NetconfMgmtError,
        NetconfMgmtOperationError,
        NetconfMgmtSessionClosedError,
    )
except ImportError as exc:  # pragma: no cover - environment problem
    sys.stderr.write(
        f"netconf: cannot import the library stack ({exc}).\n"
        f"         Run:  bash {SKILL_DIR}/scripts/setup.sh   (builds libyang, creates .venv)\n"
    )
    sys.exit(1)

ENV_FILE = ".env.ra2"
DEFAULT_PORT = 830
DEFAULT_TIMEOUT = 30
DEFAULT_MODULE = "mbm-root"
DEFAULT_MODULES = ("mbm-root", "rr-sdk-launcher")  # searched by `find` when no --module is given
NC_BASE_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"
EXIT_USAGE, EXIT_OP, EXIT_CONN = 1, 2, 3

# Commands that change device state — SKILL.md requires explicit user confirmation first.
DESTRUCTIVE_COMMANDS = ("set", "edit", "rpc")


# --------------------------------------------------------------------------- utilities


def die(msg: str, code: int = EXIT_USAGE) -> None:
    sys.stderr.write(f"netconf: {msg}\n")
    sys.exit(code)


def info(msg: str) -> None:
    sys.stderr.write(f"{msg}\n")


def dump(obj: Any, compact: bool = False) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=None if compact else 2,
                                separators=(",", ":") if compact else None) + "\n")
    sys.stdout.flush()


def pretty_xml(xml: str | bytes | Any) -> str:
    if isinstance(xml, (str, bytes)):
        try:
            xml = etree.fromstring(xml.encode() if isinstance(xml, str) else xml)
        except etree.XMLSyntaxError:
            return xml if isinstance(xml, str) else xml.decode()
    return etree.tostring(xml, pretty_print=True, encoding="unicode")


def parse_value(raw: str) -> str:
    """`@file` reads a file; everything else is the literal string (NETCONF leaves are text)."""
    if raw.startswith("@") and len(raw) > 1:
        p = Path(raw[1:])
        if not p.is_file():
            die(f"file not found: {p}")
        return p.read_text()
    return raw


def first_line(doc: str | None) -> str:
    if not doc:
        return ""
    for line in inspect.cleandoc(doc).splitlines():
        if line.strip():
            return line.strip()
    return ""


def parent_path(path: str) -> str:
    """/mbm-root:config_data/main/Main/RR_StationName -> /mbm-root:config_data/main/Main"""
    p = path.rstrip("/")
    if p.count("/") <= 1:
        die(f"path {path!r} must point below a top-level container")
    return p.rsplit("/", 1)[0]


def module_of(path: str) -> str:
    m = re.match(r"^/([A-Za-z0-9_.-]+):", path)
    if not m:
        die(f"path {path!r} must start with /module-name: (e.g. /mbm-root:config_data/...)")
    return m.group(1)


# --------------------------------------------------------------------------- connection settings


def find_env_file(start: Path) -> Path | None:
    for base in (start, SKILL_DIR):
        for d in [base, *base.parents]:
            cand = d / ENV_FILE
            if cand.is_file():
                return cand
    return None


def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        k, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


@dataclasses.dataclass
class Conn:
    host: str | None
    port: int
    user: str
    password: str
    timeout: int
    hostkey_verify: bool
    source: str

    def device(self) -> NetconfDevice:
        if not self.host:
            die("no device host: pass --host, set RA2_HOST, or create .env.ra2 (see .env.ra2.example)")
        if not (self.user and self.password):
            die("no credentials: set RA2_USER / RA2_PASSWORD (NETCONF has no public methods)", EXIT_CONN)
        return NetconfDevice(self.host, self.port, self.user, self.password, timeout=self.timeout,
                             hostkey_verify=self.hostkey_verify)

    def masked(self) -> dict[str, Any]:
        return {"host": self.host, "port": self.port, "user": self.user or None,
                "password": ("*" * 8) if self.password else None, "timeout": self.timeout,
                "hostkey_verify": self.hostkey_verify, "source": self.source,
                "python": sys.executable, "library": str(LIB_SRC),
                "libyang": getattr(libyang, "__version__", None)}


def resolve_conn(args: argparse.Namespace) -> Conn:
    env: dict[str, str] = {}
    source = "flags/environment"
    env_file = find_env_file(Path.cwd())
    if env_file:
        env.update(load_env_file(env_file))
        source = f"{env_file} (+ environment overrides)"
    env.update({k: v for k, v in os.environ.items() if k.startswith("RA2_")})

    def pick(flag: Any, key: str, default: Any = None) -> Any:
        return flag if flag is not None else env.get(key, default)

    verify_raw = pick(args.hostkey_verify, "RA2_NETCONF_HOSTKEY_VERIFY", "0")
    return Conn(
        host=pick(args.host, "RA2_HOST") or None,
        port=int(pick(args.port, "RA2_NETCONF_PORT", DEFAULT_PORT)),
        user=pick(args.user, "RA2_USER", "") or "",
        password=pick(args.password, "RA2_PASSWORD", "") or "",
        timeout=int(pick(args.timeout, "RA2_TIMEOUT", DEFAULT_TIMEOUT)),
        hostkey_verify=str(verify_raw).lower() in ("1", "true", "yes"),
        source=source,
    )


# --------------------------------------------------------------------------- error handling


def run_guarded(fn: Any) -> Any:
    try:
        return fn()
    except NetconfMgmtSessionClosedError as exc:
        die(f"session closed by device: {exc}", EXIT_CONN)
    except NetconfMgmtConnectionError as exc:
        die(f"cannot connect: {exc}", EXIT_CONN)
    except (NetconfMgmtConfigError, NetconfMgmtOperationError) as exc:
        die(f"{type(exc).__name__}: {exc}", EXIT_OP)
    except NetconfMgmtError as exc:
        die(f"{type(exc).__name__}: {exc}", EXIT_OP)
    except libyang.LibyangError as exc:
        die(f"libyang: {exc}", EXIT_OP)
    except KeyboardInterrupt:
        info("interrupted")
        sys.exit(130)


# --------------------------------------------------------------------------- schema contexts


class SchemaSource:
    """Builds libyang contexts whose modules come from the device (or the bundled example when offline)."""

    def __init__(self, dev: NetconfDevice | None) -> None:
        self.dev = dev
        self.cache: dict[str, str] = {}
        self.module_list: list[tuple[str, str | None, str | None]] = []
        if dev is not None:
            self.module_list = dev.get_module_list()
        self.available: dict[str, str | None] = {n: r for n, r, _ in self.module_list}
        self.ns_of: dict[str, str] = {n: ns for n, _, ns in self.module_list if ns}
        self.module_of_ns: dict[str, str] = {ns: n for n, ns in self.ns_of.items()}

    def yang(self, name: str, rev: str | None = None) -> str | None:
        if name in self.cache:
            return self.cache[name]
        text: str | None = None
        if self.dev is not None and name in self.available:
            try:
                text = self.dev.get_schema(name, rev or self.available[name])
            except NetconfMgmtError as exc:
                info(f"schema {name}: {exc}")
        elif self.dev is None:
            if name == DEFAULT_MODULE and EXAMPLE_YANG.is_file():
                text = EXAMPLE_YANG.read_text()
            else:
                for f in IETF_DIR.glob(f"{name}@*.yang"):
                    text = f.read_text()
                    if rev:  # the example model imports newer IETF revisions than libyang ships: shim them
                        text = text.replace("revision ", f'revision {rev} {{ description "shim"; }}\n  revision ', 1)
                    break
        if text is not None:
            self.cache[name] = text
        return text

    def context(self, modules: list[str]) -> libyang.Context:
        ctx = libyang.Context()

        def loader(mod: str | None, rev: str | None, sub: str | None, subrev: str | None) -> tuple[str, str] | None:
            text = self.yang(mod or sub or "", rev or subrev)
            return ("yang", text) if text else None

        ctx.external_module_loader.set_module_data_clb(loader)
        for name in modules:
            text = self.yang(name)
            if text is None:
                ctx.destroy()
                die(f"module {name!r} is not available" + ("" if self.dev else " offline"), EXIT_OP)
            try:
                ctx.parse_module_str(text, fmt="yang")
            except libyang.LibyangError as exc:
                ctx.destroy()
                die(f"cannot parse module {name}: {exc}", EXIT_OP)
        return ctx


def open_source(conn: Conn, offline: bool) -> tuple[NetconfDevice | None, SchemaSource]:
    if offline or not conn.host:
        if not offline:
            info("netconf: no device host configured — using the bundled mbm-root example model (offline)")
        return None, SchemaSource(None)
    dev = conn.device()
    dev.connect()
    return dev, SchemaSource(dev)


def node_type(node: libyang.SNode) -> str:
    if isinstance(node, (libyang.SLeaf, libyang.SLeafList)):
        try:
            return node.type().name()
        except libyang.LibyangError:
            return "?"
    if isinstance(node, libyang.SList):
        return "list[" + ",".join(k.name() for k in node.keys()) + "]"
    return ""


def node_row(node: libyang.SNode) -> dict[str, Any]:
    row: dict[str, Any] = {
        "path": node.data_path() or node.schema_path(),
        "kind": node.keyword(),
        "type": node_type(node),
        "config": not node.config_false(),
    }
    if isinstance(node, libyang.SLeaf):
        try:
            d = node.default()
            if d is not None:
                row["default"] = d
        except libyang.LibyangError:
            pass
        u = node.units()
        if u:
            row["units"] = u
    desc = node.description()
    if desc:
        row["description"] = " ".join(desc.split())[:200]
    return row


def enum_values(node: libyang.SNode) -> list[str]:
    if not isinstance(node, libyang.SLeaf):
        return []
    try:
        t = node.type()
        return [e[0] if isinstance(e, tuple) else str(e) for e in (t.enums() or [])] if hasattr(t, "enums") else []
    except libyang.LibyangError:
        return []


# --------------------------------------------------------------------------- commands


def cmd_env(args: argparse.Namespace, conn: Conn) -> None:
    dump(conn.masked(), args.compact)


def cmd_ping(args: argparse.Namespace, conn: Conn) -> None:
    dev = conn.device()

    def go() -> None:
        t0 = time.monotonic()
        with dev:
            mgr = dev._active_manager  # noqa: SLF001
            caps = sorted(str(c) for c in mgr.server_capabilities)
            ms = round((time.monotonic() - t0) * 1000)
            short = [c for c in caps if "netconf" in c and "module=" not in c]
            modules = len([c for c in caps if "module=" in c])
            out: dict[str, Any] = {"host": conn.host, "port": conn.port, "session_id": mgr.session_id,
                                   "connect_ms": ms, "netconf_capabilities": short, "yang_modules_in_hello": modules}
            if args.all:
                out["all_capabilities"] = caps
            dump(out, args.compact)

    run_guarded(go)


def cmd_modules(args: argparse.Namespace, conn: Conn) -> None:
    dev = conn.device()
    pat = re.compile(args.pattern, re.I) if args.pattern else None

    def go() -> None:
        with dev:
            rows = [{"name": n, "revision": r, "namespace": ns} for n, r, ns in dev.get_module_list()
                    if not pat or pat.search(n) or pat.search(ns or "")]
        dump(rows, args.compact)

    run_guarded(go)


def cmd_schema(args: argparse.Namespace, conn: Conn) -> None:
    dev = conn.device()

    def go() -> None:
        with dev:
            text = dev.get_schema(args.module, args.revision)
        if args.out:
            Path(args.out).write_text(text)
            info(f"wrote {args.out} ({len(text)} bytes)")
        else:
            sys.stdout.write(text if text.endswith("\n") else text + "\n")

    run_guarded(go)


def render_subtree(root: libyang.SNode, depth: int | None) -> Iterator[str]:
    base = root.schema_path().count("/")

    def walk(node: libyang.SNode, level: int) -> Iterator[str]:
        flag = "ro" if node.config_false() else "rw"
        t = node_type(node)
        kw = node.keyword()
        name = node.name() + ("?" if kw == "leaf" and not node.mandatory() else "")
        yield f"{'  ' * level}+--{flag} {name}{'  ' + t if t else ''}{'  [' + kw + ']' if kw not in ('leaf', 'container') else ''}"
        if depth is not None and level + 1 > depth:
            return
        if hasattr(node, "children"):
            for ch in node.children():  # type: ignore[attr-defined]
                yield from walk(ch, level + 1)

    _ = base
    yield from walk(root, 0)


def cmd_tree(args: argparse.Namespace, conn: Conn) -> None:
    def go() -> None:
        dev, src = open_source(conn, args.offline)
        try:
            target = args.target or DEFAULT_MODULE
            if target.startswith("/"):
                module = module_of(target)
                ctx = src.context([module])
                try:
                    nodes = list(ctx.find_path(target))
                    if not nodes:
                        die(f"no schema node at {target}", EXIT_OP)
                    for n in nodes:
                        sys.stdout.write(f"{n.data_path()}\n")
                        for line in render_subtree(n, args.depth):
                            sys.stdout.write(line + "\n")
                finally:
                    ctx.destroy()
            else:
                ctx = src.context([target])
                try:
                    mod = ctx.get_module(target)
                    text = mod.print("tree", libyang.IOType.MEMORY)
                    if args.depth is not None:
                        # tree lines nest by 3 chars per level after the leading "module:" line
                        kept = []
                        for line in (text or "").splitlines():
                            indent = len(line) - len(line.lstrip(" |"))
                            if indent // 3 <= args.depth:
                                kept.append(line)
                        text = "\n".join(kept) + "\n"
                    sys.stdout.write(text or "")
                finally:
                    ctx.destroy()
        finally:
            if dev is not None:
                dev.close()

    run_guarded(go)


def cmd_find(args: argparse.Namespace, conn: Conn) -> None:
    needle = args.text.lower()

    def go() -> None:
        dev, src = open_source(conn, args.offline)
        try:
            if args.module:
                modules = [args.module]
            elif src.dev is None:
                modules = [DEFAULT_MODULE]
            else:
                modules = [m for m in DEFAULT_MODULES if m in src.available] or sorted(src.ns_of)
            ctx = src.context(modules)
            try:
                rows: list[dict[str, Any]] = []
                for name in modules:
                    mod = ctx.get_module(name)
                    for top in mod.children():
                        for node in top.iter_tree():
                            hay = node.name().lower()
                            desc = (node.description() or "").lower() if args.desc else ""
                            if needle in hay or (desc and needle in desc):
                                row = node_row(node)
                                ev = enum_values(node)
                                if ev:
                                    row["enum"] = ev
                                rows.append(row)
                                if len(rows) >= args.limit:
                                    break
                        if len(rows) >= args.limit:
                            break
                if not rows:
                    info(f"no schema node matches {args.text!r}" + ("" if args.desc else " (try --desc to search descriptions)"))
                dump(rows, args.compact)
            finally:
                ctx.destroy()
        finally:
            if dev is not None:
                dev.close()

    run_guarded(go)


def cmd_get(args: argparse.Namespace, conn: Conn) -> None:
    dev = conn.device()

    def go() -> None:
        with dev:
            cfg = dev.get_config(xpath=args.xpath, datastore=args.datastore)
            if args.xml:
                sys.stdout.write(pretty_xml(f"<_>{cfg.xml}</_>").replace("<_>", "").replace("</_>", "").strip() + "\n")
            else:
                dump(cfg.as_dict(), args.compact)

    run_guarded(go)


def cmd_leaf(args: argparse.Namespace, conn: Conn) -> None:
    dev = conn.device()

    def go() -> None:
        with dev:
            cfg = dev.get_config(xpath=parent_path(args.path), datastore=args.datastore)
            dump({args.path: cfg.get(args.path)}, args.compact)

    run_guarded(go)


def cmd_set(args: argparse.Namespace, conn: Conn) -> None:
    if not args.assignments:
        die("set: give PATH=VALUE assignments")
    assignments: dict[str, str] = {}
    for item in args.assignments:
        if "=" not in item:
            die(f"set: expected PATH=VALUE, got {item!r}")
        path, _, raw = item.partition("=")
        assignments[path.strip()] = parse_value(raw)
    by_parent: dict[str, dict[str, str]] = {}
    for path, value in assignments.items():
        by_parent.setdefault(parent_path(path), {})[path] = value
    dev = conn.device()
    info(f"netconf: edit-config ({args.operation}) on datastore {args.datastore!r}")

    def go() -> None:
        report: dict[str, Any] = {}
        with dev:
            for parent, items in by_parent.items():
                cfg = dev.get_config(xpath=parent, datastore=args.datastore)
                for path, value in items.items():
                    old = cfg.get(path)
                    cfg.set(path, value)
                    report[path] = {"old": old, "new": value}
                dev.set_config(cfg, operation=args.operation, datastore=args.datastore)
                fresh = dev.get_config(xpath=parent, datastore=args.datastore)
                for path in items:
                    report[path]["device"] = fresh.get(path)
        dump(report, args.compact)

    run_guarded(go)


def cmd_edit(args: argparse.Namespace, conn: Conn) -> None:
    p = Path(args.file)
    if not p.is_file():
        die(f"file not found: {p}")
    text = p.read_text()
    dev = conn.device()
    info(f"netconf: edit-config ({args.operation}) on datastore {args.datastore!r} from {p}")

    def go() -> None:
        with dev:
            if text.lstrip().startswith("<"):
                cfg = NetconfConfig(text)
            else:
                data = json.loads(text)
                if not isinstance(data, dict) or not data:
                    die("edit: JSON must be a non-empty object in as_dict() format ({'module:node': {...}})")
                modules = sorted({k.split(":", 1)[0] for k in data if ":" in k})
                if not modules:
                    die("edit: JSON top-level keys must be module-prefixed (e.g. 'mbm-root:config_data')")
                cfg = dev.get_config(xpath=f"/{modules[0]}:*", datastore=args.datastore)
                if len(modules) > 1:
                    # schemas of the other modules are needed to convert the dict; borrow them
                    src = SchemaSource(dev)
                    for m in modules[1:]:
                        text = src.yang(m)
                        if text is None:
                            die(f"edit: module {m!r} is not available on the device", EXIT_OP)
                        cfg._dep_yang[m] = text  # noqa: SLF001
                cfg.update_from_dict(data)
            dev.set_config(cfg, operation=args.operation, datastore=args.datastore)
        dump({"status": "ok", "operation": args.operation, "datastore": args.datastore, "bytes": len(cfg.xml)}, args.compact)

    run_guarded(go)


def cmd_state(args: argparse.Namespace, conn: Conn) -> None:
    dev = conn.device()

    def go() -> None:
        with dev:
            src = SchemaSource(dev)
            mgr = dev._active_manager  # noqa: SLF001
            if args.xpath:
                xmlns: dict[str, str] = {}
                for prefix in set(re.findall(r"/([A-Za-z0-9_.-]+):", args.xpath)):
                    if prefix not in src.ns_of:
                        die(f"XPath prefix {prefix!r} is not a module on the device (see `netconf modules`)", EXIT_OP)
                    xmlns[prefix] = src.ns_of[prefix]
                reply = mgr.get(filter=("xpath", (xmlns, args.xpath)))
            else:
                reply = mgr.get()
            nodes = list(reply.data_ele)
            if not nodes:
                info("no data returned")
                dump({} if args.json else None, args.compact) if args.json else None
                return
            if not args.json:
                for n in nodes:
                    sys.stdout.write(pretty_xml(n))
                return
            namespaces = {etree.QName(n).namespace for n in nodes if etree.QName(n).namespace}
            modules = [src.module_of_ns[ns] for ns in namespaces if ns in src.module_of_ns]
            ctx = src.context(modules)
            try:
                xml = "".join(etree.tostring(n, encoding="unicode") for n in nodes)
                dnode = ctx.parse_data_mem(xml, fmt="xml", parse_only=True)
                if dnode is None:
                    die("libyang could not parse the returned data", EXIT_OP)
                try:
                    dump(json.loads(dnode.print_mem("json", with_siblings=True)), args.compact)
                finally:
                    dnode.free()
            finally:
                ctx.destroy()

    run_guarded(go)


def cmd_rpc(args: argparse.Namespace, conn: Conn) -> None:
    xml = parse_value(args.xml)
    try:
        element = etree.fromstring(xml.encode())
    except etree.XMLSyntaxError as exc:
        die(f"rpc: invalid XML: {exc}")
    dev = conn.device()
    info(f"netconf: dispatching <{etree.QName(element).localname}>")

    def go() -> None:
        with dev:
            reply = dev._active_manager.dispatch(element)  # noqa: SLF001
        sys.stdout.write(pretty_xml(reply.xml))

    run_guarded(go)


def library_methods() -> list[tuple[str, str, Any]]:
    rows: list[tuple[str, str, Any]] = []
    for cls in (NetconfDevice, NetconfConfig):
        for name, fn in inspect.getmembers(cls, lambda m: inspect.isfunction(m) or isinstance(m, property)):
            if name.startswith("_"):
                continue
            rows.append((cls.__name__, name, fn))
        for name, member in vars(cls).items():
            if isinstance(member, classmethod) and not name.startswith("_"):
                rows.append((cls.__name__, name, member.__func__))
    return rows


def signature_text(fn: Any) -> str:
    if isinstance(fn, property):
        return " (property)"
    sig = inspect.signature(fn)
    params = [p for n, p in sig.parameters.items() if n not in ("self", "cls")]
    text = str(sig.replace(parameters=params))
    for prefix in ("rr_netconf_mgmt.config.", "rr_netconf_mgmt.device.", "typing.", "collections.abc."):
        text = text.replace(prefix, "")
    return text


def cmd_methods(args: argparse.Namespace, conn: Conn) -> None:
    pat = re.compile(args.pattern, re.I) if args.pattern else None
    current = None
    for cls, name, fn in library_methods():
        doc = fn.fget.__doc__ if isinstance(fn, property) else fn.__doc__
        if pat and not (pat.search(name) or pat.search(first_line(doc))):
            continue
        if cls != current:
            current = cls
            sys.stdout.write(f"\n## {cls}\n")
        flag = "  [DESTRUCTIVE]" if name in ("set_config",) else ""
        sys.stdout.write(f"{name}{signature_text(fn)}{flag}\n")
        if first_line(doc):
            sys.stdout.write(f"    {first_line(doc)}\n")


def cmd_doc(args: argparse.Namespace, conn: Conn) -> None:
    found = False
    for cls, name, fn in library_methods():
        if name == args.name:
            found = True
            doc = fn.fget.__doc__ if isinstance(fn, property) else fn.__doc__
            sys.stdout.write(f"### {cls}.{name}{signature_text(fn)}\n{inspect.cleandoc(doc or '')}\n\n")
    if not found:
        die(f"no library method named {args.name!r}; see `netconf methods`")


# --------------------------------------------------------------------------- argument parsing


GLOBAL_DEFAULTS: dict[str, Any] = {'host': None, 'port': None, 'user': None, 'password': None, 'timeout': None, 'hostkey_verify': None, 'offline': False, 'compact': False, 'datastore': 'running'}


def _sub_parser_class(common: argparse.ArgumentParser) -> type[argparse.ArgumentParser]:
    """Subparsers that also accept the global options, so `CMD --flag` works like `--flag CMD`."""

    class _Sub(argparse.ArgumentParser):
        def __init__(self, **kwargs: Any) -> None:
            kwargs.setdefault("parents", [common])
            super().__init__(**kwargs)

    return _Sub


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    g = common.add_argument_group("connection (override RA2_* env / .env.ra2)")
    g.add_argument("--host")
    g.add_argument("--port", type=int)
    g.add_argument("--user")
    g.add_argument("--password")
    g.add_argument("--timeout", type=int, help=f"SSH/RPC timeout in seconds (default {DEFAULT_TIMEOUT})")
    g.add_argument("--hostkey-verify", dest="hostkey_verify", action="store_const", const="1",
                   help="verify the SSH host key against known_hosts (default: off, devices have per-unit keys)")
    g.add_argument("--offline", action="store_true", help="tree/find: use the bundled mbm-root example model, no device")
    o = common.add_argument_group("output")
    o.add_argument("--compact", "-c", action="store_true", help="single-line JSON")
    o.add_argument("--datastore", default="running", help="NETCONF datastore (default running)")

    p = argparse.ArgumentParser(parents=[common], prog="netconf", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND", parser_class=_sub_parser_class(common))
    sub.add_parser("env", help="show resolved connection settings").set_defaults(func=cmd_env)

    a = sub.add_parser("ping", help="open a NETCONF session and show session id + capabilities")
    a.add_argument("--all", action="store_true", help="list every capability URI")
    a.set_defaults(func=cmd_ping)

    a = sub.add_parser("modules", help="YANG modules advertised via NETCONF monitoring")
    a.add_argument("pattern", nargs="?")
    a.set_defaults(func=cmd_modules)

    a = sub.add_parser("schema", help="raw YANG module text (get-schema)")
    a.add_argument("module")
    a.add_argument("--revision")
    a.add_argument("--out", metavar="FILE")
    a.set_defaults(func=cmd_schema)

    a = sub.add_parser("tree", help="schema tree of a module or below a data path")
    a.add_argument("target", nargs="?", help=f"module name (default {DEFAULT_MODULE}) or /module:path")
    a.add_argument("--depth", type=int)
    a.set_defaults(func=cmd_tree)

    a = sub.add_parser("find", help="search schema nodes by name (and description with --desc)")
    a.add_argument("text")
    a.add_argument("--module", help=f"restrict to one module (default: {', '.join(DEFAULT_MODULES)} when present, else all)")
    a.add_argument("--desc", action="store_true")
    a.add_argument("--limit", type=int, default=50)
    a.set_defaults(func=cmd_find)

    a = sub.add_parser("get", help="get-config as JSON (default) or XML: netconf get /mbm-root:config_data/main/Main")
    a.add_argument("xpath", nargs="?")
    a.add_argument("--xml", action="store_true")
    a.set_defaults(func=cmd_get)

    a = sub.add_parser("leaf", help="typed value of one leaf: netconf leaf /mbm-root:config_data/main/Main/RR_StationName")
    a.add_argument("path")
    a.set_defaults(func=cmd_leaf)

    a = sub.add_parser("set", help="set leaves and write back (edit-config): netconf set /mbm-root:.../RR_StationName=Unit7")
    a.add_argument("assignments", nargs="*")
    a.add_argument("--operation", choices=("merge", "replace"), default="merge")
    a.set_defaults(func=cmd_set)

    a = sub.add_parser("edit", help="edit-config from a file: XML fragment or JSON in as_dict() format")
    a.add_argument("file")
    a.add_argument("--operation", choices=("merge", "replace"), default="merge")
    a.set_defaults(func=cmd_edit)

    a = sub.add_parser("state", help="<get> operational data, XML (default) or --json")
    a.add_argument("xpath", nargs="?", help="e.g. /mbm-root:Diagnostics")
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_state)

    a = sub.add_parser("rpc", help="dispatch an arbitrary RPC element: netconf rpc '<get-schema xmlns=...>...</get-schema>' or @file.xml")
    a.add_argument("xml")
    a.set_defaults(func=cmd_rpc)

    a = sub.add_parser("methods", help="library methods (NetconfDevice, NetconfConfig)")
    a.add_argument("pattern", nargs="?")
    a.set_defaults(func=cmd_methods)

    a = sub.add_parser("doc", help="docstring of a library method")
    a.add_argument("name")
    a.set_defaults(func=cmd_doc)
    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    for key, value in GLOBAL_DEFAULTS.items():
        if not hasattr(args, key):
            setattr(args, key, value)
    conn = resolve_conn(args)
    args.func(args, conn)


if __name__ == "__main__":
    main()
