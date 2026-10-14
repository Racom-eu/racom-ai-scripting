"""Offline policy tests through real RACOM CLI entry points.

When NETCONF dependencies are absent, the subprocess harness installs test-only
stubs. These test policy/dispatch, not the real NETCONF transport or lxml behavior.
"""

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import racom_safety

# Independent audited expectations, rather than deriving cases from policy.
READ_RPC = """
events_get events_meta_get messages_get
settings_get settings_get_default settings_package_get icmp_ping_poll
device_info_get_public device_info_get_private device_capabilities_get
device_constants_get device_ping diagnostics_overview_get
diagnostic_package_status_get status_overview_data_get status_overview_card_get
status_info_data_get status_info_schema_get status_info_presentation_get
alarm_overview_data_get alarm_overview_card_get statistics_meta_get
statistics_data_get statistics_presentation_get statistics_csv_get
statistics_differential_data_get monitoring_schema_get
monitoring_live_poll_params_get monitoring_live_data_get monitoring_config_get
monitoring_config_get_default monitoring_log_status_get monitoring_log_data_get
firmware_status_get firmware_module_status_get file_distribution_status_send_get
file_distribution_status_receive_get file_repository_status_get
keyring_status_get keyring_secret_status_get keyring_secret_download
keyring_backup_get users_get user_attributes_get users_backup_get
script_overview_get script_execution_get script_execution_output_get
script_execution_filter_options_get log_stream_filter_options_get
log_stream_poll rss_ping_poll ip_route_get radio_freq_tuning_get
""".split()

WRITE_RPC = """
event_test_trigger settings_save_init settings_save_reconnect
settings_package_save_init time_set icmp_ping_start icmp_ping_stop
diagnostic_package_start diagnostic_package_file_get statistics_differential_reset
monitoring_config_save monitoring_log_start monitoring_log_stop
monitoring_log_delete firmware_upload_init firmware_upload_reconnect
firmware_activate_init firmware_activate_reconnect firmware_module_activate_init
firmware_module_activate_reconnect file_distribution_start file_distribution_pause
file_distribution_resume file_distribution_abort reboot_init reboot_reconnect
reset_factory reset_total_purge tamper_reset_init tamper_reset_reconnect
keyring_secret_add keyring_secret_delete keyring_secret_generate_init
keyring_secret_update keyring_secret_upload_init keyring_reconnect
keyring_backup_restore_init keyring_csr_sign keyring_key_csr_download
user_create user_update user_delete user_password_change change_password
users_backup_restore script_start script_stop log_stream_start log_stream_stop
rss_ping_start rss_ping_stop keying_start keying_stop sw_key_upload
antenna_detector_calibrate antenna_detector_restart hstdby_toggle
radio_freq_tuning_set keyring_secret_update_init
""".split()

READ_HELPER = """
monitoring_live monitoring_log statistics statistics_differential status_info
""".split()
WRITE_HELPER = """
settings_save settings_package_save reboot firmware_upload firmware_activate
firmware_module_activate tamper_reset keyring_backup_restore
keyring_secret_generate keyring_secret_upload diagnostic_package
icmp_ping rss_ping log_stream
""".split()
SESSION = ["connect", "close", "reconnect"]
DOCS_ONLY = {
    "radio_freq_tuning_get", "radio_freq_tuning_set",
    "keyring_secret_update_init",
}
NC = "urn:ietf:params:xml:ns:netconf:base:1.0"
MON = "urn:ietf:params:xml:ns:yang:ietf-netconf-monitoring"
ENTRY = {
    "ra2": [
        [str(ROOT / "skills/api/scripts/ra2")],
        [sys.executable, str(ROOT / "skills/api/scripts/ra2")],
    ],
    "netconf": [
        [str(ROOT / "skills/netconf/scripts/netconf")],
        [sys.executable, str(ROOT / "skills/netconf/scripts/netconf_cli.py")],
    ],
    "scripting": [
        [str(ROOT / "skills/scripting/scripts/scripting")],
        [sys.executable, str(ROOT / "skills/scripting/scripts/scripting_cli.py")],
    ],
}

# Created only in a temporary test directory when tests run.
BOOTSTRAP = r'''
import importlib.util
import json
import os
from pathlib import Path
import sys
import types


def event(name):
    with open(os.environ["RACOM_TEST_EVENTS"], "a") as stream:
        stream.write(json.dumps(name) + "\n")


def denied(*args, **kwargs):
    event("forbidden")
    raise AssertionError("Offline test forbids device/network activity")


def audit(name, args):
    if name in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
                "socket.gethostbyaddr", "socket.bind", "socket.sendto"}:
        denied()


sys.addaudithook(audit)
root = Path(os.environ["RACOM_TEST_ROOT"])
for path in (
    root / "scripts",
    root / "skills/api/ra2_mgmt/src",
    root / "skills/netconf/rac_netconf_mgmt/src",
):
    sys.path.insert(0, str(path))

import socket
import urllib.request
socket.create_connection = denied
socket.getaddrinfo = denied
urllib.request.urlopen = denied

# Mock the API dependency boundary; the real CLI parser and guard still run.
def test_module(name, package=False):
    value = types.ModuleType(name)
    if package:
        value.__path__ = []
    sys.modules[name] = value
    return value

api = test_module("rr_ra2_mgmt", True)
transport = test_module("rr_ra2_mgmt.transport")
api.transport = transport
for name in ("login", "logout", "rpc"):
    setattr(transport, name, denied)
device = test_module("rr_ra2_mgmt.device")
device.Device = type("Device", (), {"__init__": denied})
helper = test_module("rr_ra2_mgmt.helper")
helper.Helper = type("Helper", (), {})
models = test_module("rr_ra2_mgmt.models")
models.Envelope = type("Envelope", (), {})
exceptions = test_module("rr_ra2_mgmt.exceptions")
base = type("Ra2MgmtError", (Exception,), {})
exceptions.Ra2MgmtError = base
for name in ("Ra2CommunicationError", "Ra2LoginError", "Ra2RpcError",
             "Ra2SessionExpiredError"):
    setattr(exceptions, name, type(name, (base,), {}))

# Test-only fallback: no downloads or edits to production dependencies.
if not all(importlib.util.find_spec(name) for name in ("libyang", "ncclient", "lxml")):
    import xml.etree.ElementTree as ET

    def module(name, package=False):
        value = types.ModuleType(name)
        if package:
            value.__path__ = []
        sys.modules[name] = value
        return value

    libyang = module("libyang")
    libyang.Context = denied
    ncclient = module("ncclient", True)
    ncclient.manager = module("ncclient.manager")
    ncclient.manager.connect = denied
    lxml = module("lxml", True)
    etree = module("lxml.etree")
    lxml.etree = etree
    etree.XMLSyntaxError = ET.ParseError

    class XMLParser:
        def __init__(self, **options):
            assert options == dict(resolve_entities=False, load_dtd=False,
                                   no_network=True)

    class Element:
        def __init__(self, node, data):
            self.tag = node.tag
            self.docinfo = types.SimpleNamespace(
                doctype="present" if b"<!DOCTYPE" in data else "")

        def getroottree(self):
            return self

    def fromstring(data, parser):
        assert isinstance(parser, XMLParser)
        return Element(ET.fromstring(data), data)

    etree.XMLParser = XMLParser
    etree.fromstring = fromstring
    module("rr_netconf_mgmt", True)
    config = module("rr_netconf_mgmt.config")
    config.NetconfConfig = type("NetconfConfig", (), {"__init__": denied})
    device = module("rr_netconf_mgmt.device")
    device.NetconfDevice = type("NetconfDevice", (), {"__init__": denied})
    exceptions = module("rr_netconf_mgmt.exceptions")
    base = type("NetconfMgmtError", (Exception,), {})
    exceptions.NetconfMgmtError = base
    for name in ("NetconfMgmtConfigError", "NetconfMgmtConnectionError",
                 "NetconfMgmtOperationError", "NetconfMgmtSessionClosedError"):
        setattr(exceptions, name, type(name, (base,), {}))
    event("netconf-stubs")
else:
    from rr_netconf_mgmt.device import NetconfDevice
    import ncclient.manager
    import libyang
    NetconfDevice.__init__ = denied
    ncclient.manager.connect = denied
    libyang.Context = denied

import racom_safety
original_guard = racom_safety.guard_cli


def checked_dispatch(tool, args, **kwargs):
    original_guard(tool, args, **kwargs)
    original_handler = args.func

    def record_handler(parsed, connection):
        if tool == "ra2" and parsed.cmd in {"call", "helper", "rpc"}:
            original_handler.__globals__["parse_params"](parsed.params)
        if tool == "netconf" and parsed.cmd == "rpc":
            assert parsed.rpc_element.tag == kwargs["netconf_rpc_tag"]
        event("dispatch")

    args.func = record_handler


racom_safety.guard_cli = checked_dispatch
# A bootstrap exception must fail closed rather than let the CLI run untrapped.
event("ready")
'''


def public_methods(path, class_name):
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    return {node.name for node in cls.body
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")}


def allowed_cases():
    cases = [
        ("ra2", ["env"]), ("ra2", ["methods"]),
        ("ra2", ["methods", "--json"]),
        ("ra2", ["doc", "settings_get"]),
        ("ra2", ["doc", "--search", "settings"]),
        ("ra2", ["doc", "--list"]),
        ("ra2", ["doc", "settings_get", "--full", "--refresh"]),
        ("ra2", ["ping"]), ("ra2", ["status", "system_basic"]),
        ("ra2", ["config", "get"]), ("ra2", ["config", "keys"]),
        ("ra2", ["config", "meta", "RR_StationName"]),
    ]
    for name in READ_RPC:
        cases.append(("ra2", ["rpc", name]))
        cases.append(("ra2", ["rpc", name, "--public"]))
        cases.append(("ra2", ["rpc", name, "filter=@params.json"]))
        if name not in DOCS_ONLY:
            cases.append(("ra2", ["call", name]))
    cases.extend(("ra2", ["call", name]) for name in SESSION)
    cases.extend(("ra2", ["helper", name]) for name in READ_HELPER)
    cases.extend([
        ("netconf", ["env"]), ("netconf", ["methods"]),
        ("netconf", ["doc", "get_config"]), ("netconf", ["ping"]),
        ("netconf", ["ping", "--all"]), ("netconf", ["modules"]),
        ("netconf", ["schema", "mbm-root"]),
        ("netconf", ["schema", "mbm-root", "--out", "unused.yang"]),
        ("netconf", ["tree"]), ("netconf", ["tree", "--offline"]),
        ("netconf", ["find", "RR_StationName"]),
        ("netconf", ["find", "RR_StationName", "--offline"]),
        ("netconf", ["get"]), ("netconf", ["get", "--xml"]),
        ("netconf", ["leaf", "/mbm-root:config_data/main/Main/RR_StationName"]),
        ("netconf", ["state"]), ("netconf", ["state", "--json"]),
    ])
    for name, namespace in [("get", NC), ("get-config", NC), ("get-schema", MON)]:
        cases.append(("netconf", ["rpc", f'<{name} xmlns="{namespace}"/>']))
        cases.append(("netconf", ["rpc", f"@{name}.xml"]))
    cases.extend([
        ("scripting", ["env"]), ("scripting", ["new", "sample.py"]),
        ("scripting", ["new", "sample.py", "--kind", "continuous", "--netconf",
                       "--out", "-", "--force"]),
        ("scripting", ["check", "sample.py"]),
        ("scripting", ["check", "sample.py", "--firmware", "2.3.10.0"]),
        ("scripting", ["modules"]), ("scripting", ["list"]),
        ("scripting", ["logs", "sample.py", "instance"]),
        ("scripting", ["logs", "sample.py", "instance", "--follow", "--execution", "1"]),
        ("scripting", ["executions", "sample.py"]),
        ("scripting", ["executions", "sample.py", "instance", "--limit", "1"]),
        ("scripting", ["enable"]),
        ("scripting", ["run", "sample.py", "instance", "--no-start"]),
        ("scripting", ["run", "sample.py", "instance", "--no-start", "--follow"]),
        ("scripting", ["deploy", "sample.py", "--dry-run"]),
        ("scripting", ["deploy", "sample.py", "--dry-run", "--firmware", "2.3.10.0"]),
        ("scripting", ["undeploy", "sample.py", "--dry-run"]),
    ])
    return cases


def blocked_cases():
    cases = [("ra2", ["config", "set", "main.RR_StationName=sample"])]
    for name in WRITE_RPC:
        for params in ([], ["config_data=@params.json"], ["base64=@binary.dat"]):
            cases.append(("ra2", ["rpc", name, *params]))
            cases.append(("ra2", ["rpc", "--public", name, *params]))
        if name not in DOCS_ONLY:
            cases.append(("ra2", ["call", name]))
            cases.append(("ra2", ["call", name, "config_data=@params.json"]))
    for name in WRITE_HELPER:
        cases.append(("ra2", ["helper", name]))
        cases.append(("ra2", ["helper", name, "config_data=@params.json"]))
    for name in ["unknown_get", "unknown_set", "future_operation", "connect"]:
        cases.append(("ra2", ["rpc", name]))
    cases.extend([
        ("ra2", ["call", "unknown_get"]), ("ra2", ["helper", "unknown_get"]),
        ("ra2", ["--token", "offline-test", "--intermediate", "invalid",
                 "rpc", "reset_factory", "--public"]),
        ("ra2", ["rpc", "reset_factory", "config_data=@missing.json"]),
    ])
    for operation in ("merge", "replace"):
        for datastore in ("running", "candidate"):
            cases.append(("netconf", ["--datastore", datastore, "set", "--operation",
                operation, "/mbm-root:config_data/main/Main/RR_StationName=sample"]))
            for file in ("edit.xml", "edit.json"):
                cases.append(("netconf", ["--datastore", datastore, "edit", file,
                                          "--operation", operation]))
    for name in ["edit-config", "copy-config", "delete-config", "commit",
                 "discard-changes", "lock", "unlock", "kill-session", "close-session",
                 "unknown"]:
        cases.append(("netconf", ["rpc", f'<{name} xmlns="{NC}"/>']))
        cases.append(("netconf", ["rpc", f"@{name}.xml"]))
    for xml in ["<get/>", '<get xmlns="urn:unexpected"/>',
                f'<get-schema xmlns="{NC}"/>', f'<rpc xmlns="{NC}"><get/></rpc>',
                '<action xmlns="urn:ietf:params:xml:ns:yang:1"><get/></action>',
                '<sdk-launcher xmlns="eu:racom:common:rr-sdk-launcher">'
                '<triggers><start><manual/></start></triggers></sdk-launcher>']:
        cases.append(("netconf", ["rpc", xml]))
    cases.append(("netconf", ["set", "--offline", "/mbm-root:x=y"]))
    cases.extend([
        ("scripting", ["deploy", "sample.py"]),
        ("scripting", ["deploy", "sample.py", "--skip-check"]),
        ("scripting", ["deploy", "sample.py", "--firmware", "2.3.10.0", "--instance",
                       "instance", "--role", "admin", "--set", "arguments=--sample"]),
        ("scripting", ["deploy", "missing.py"]),
        ("scripting", ["undeploy", "sample.py"]),
        ("scripting", ["enable", "--on"]), ("scripting", ["enable", "--off"]),
        ("scripting", ["start", "sample.py", "instance"]),
        ("scripting", ["stop", "sample.py", "instance"]),
        ("scripting", ["run", "sample.py", "instance"]),
        ("scripting", ["run", "sample.py", "instance", "--follow"]),
        ("scripting", ["modules", "--fetch"]),
        ("scripting", ["modules", "--fetch", "--keep"]),
        ("scripting", ["modules", "--fetch", "--instance", "probe"]),
    ])
    return cases


class ClassificationTests(unittest.TestCase):
    def test_ra2_classifications_preserve_guard_decisions(self):
        cases = []
        for context in ("call", "rpc"):
            cases.extend((context, name, "DEVICE READ") for name in READ_RPC)
            cases.extend((context, name, "WRITE AUTH REQUIRED") for name in WRITE_RPC)
        cases.extend(("call", name, "DEVICE READ") for name in SESSION)
        cases.extend(("rpc", name, "WRITE AUTH REQUIRED") for name in SESSION)
        cases.extend(("helper", name, "DEVICE READ") for name in READ_HELPER)
        cases.extend(("helper", name, "WRITE AUTH REQUIRED") for name in WRITE_HELPER)
        cases.append(("rpc", "statistics", "WRITE AUTH REQUIRED"))
        for context in ("call", "rpc", "helper", "unknown-context"):
            cases.append((context, "future_get", "WRITE AUTH REQUIRED"))

        with mock.patch.dict(os.environ, {"RACOM_ALLOW_WRITE": "0"}):
            for context, name, expected in cases:
                with self.subTest(context=context, name=name):
                    self.assertEqual(
                        racom_safety.classify_method("ra2", context, name), expected)
                    args = argparse.Namespace(cmd=context, method=name, name=name)
                    if expected == "DEVICE READ":
                        racom_safety.guard_cli("ra2", args)
                    else:
                        with contextlib.redirect_stderr(io.StringIO()):
                            with self.assertRaises(SystemExit) as error:
                                racom_safety.guard_cli("ra2", args)
                        self.assertEqual(error.exception.code, 5)

    def test_netconf_labels_and_contexts(self):
        cases = [
            ("NetconfConfig", "__init__", "LOCAL ONLY"),
            ("NetconfConfig", "as_dict", "LOCAL ONLY"),
            ("NetconfConfig", "set", "LOCAL ONLY"),
            ("NetconfConfig", "update_from_dict", "LOCAL ONLY"),
            ("NetconfConfig", "get", "LOCAL ONLY"),
            ("NetconfConfig", "xml", "LOCAL ONLY"),
            ("NetconfConfig", "from_device", "DEVICE READ"),
            ("NetconfDevice", "__init__", "LOCAL ONLY"),
            ("NetconfDevice", "__enter__", "DEVICE READ"),
            ("NetconfDevice", "connect", "DEVICE READ"),
            ("NetconfDevice", "close", "DEVICE READ"),
            ("NetconfDevice", "reconnect", "DEVICE READ"),
            ("NetconfDevice", "get_config", "DEVICE READ"),
            ("NetconfDevice", "get_schema", "DEVICE READ"),
            ("NetconfDevice", "fetch_config_xml", "DEVICE READ"),
            ("NetconfDevice", "get_module_list", "DEVICE READ"),
            ("NetconfDevice", "set_config", "WRITE AUTH REQUIRED"),
            ("NetconfDevice", "set", "WRITE AUTH REQUIRED"),
            ("NetconfDevice", "future_get", "WRITE AUTH REQUIRED"),
            ("SchemaFetcherProtocol", "get_schema", "WRITE AUTH REQUIRED"),
            ("unknown-context", "get_schema", "WRITE AUTH REQUIRED"),
        ]
        for context, name, expected in cases:
            with self.subTest(context=context, name=name):
                self.assertEqual(
                    racom_safety.classify_method("netconf", context, name), expected)
        self.assertEqual(
            racom_safety.classify_method("future-tool", "call", "settings_get"),
            "WRITE AUTH REQUIRED")


class MethodPresentationTests(unittest.TestCase):
    def setUp(self):
        # Inspect/list/render only. Any attempted network access fails the test.
        for target in ("socket.socket.connect", "socket.create_connection",
                       "socket.getaddrinfo", "urllib.request.urlopen"):
            patcher = mock.patch(
                target, side_effect=AssertionError("Network access forbidden"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def load(self, relative, module_name):
        loader = importlib.machinery.SourceFileLoader(
            module_name, str(ROOT / relative))
        spec = importlib.util.spec_from_loader(module_name, loader)
        module = importlib.util.module_from_spec(spec)
        patcher = mock.patch.dict(sys.modules, {module_name: module})
        patcher.start()
        self.addCleanup(patcher.stop)
        loader.exec_module(module)
        return module

    def test_api_listing_and_catalog(self):
        cli = self.load("skills/api/scripts/ra2", "test_ra2_presentation")
        generator = self.load(
            "skills/api/scripts/gen_catalog.py", "test_api_catalog")
        args = argparse.Namespace(pattern=None, json=True, compact=False)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cli.cmd_methods(args, None)
        rows = {(row["kind"], row["name"]): row
                for row in json.loads(output.getvalue())}
        cases = [
            ("device", "connect", "DEVICE READ"),
            ("device", "settings_get", "DEVICE READ"),
            ("device", "icmp_ping_start", "WRITE AUTH REQUIRED"),
            ("device", "diagnostic_package_file_get", "WRITE AUTH REQUIRED"),
            ("helper", "statistics", "DEVICE READ"),
            ("helper", "icmp_ping", "WRITE AUTH REQUIRED"),
            ("helper", "rss_ping", "WRITE AUTH REQUIRED"),
            ("helper", "log_stream", "WRITE AUTH REQUIRED"),
            ("helper", "diagnostic_package", "WRITE AUTH REQUIRED"),
            ("docs-only", "radio_freq_tuning_get", "DEVICE READ"),
            ("docs-only", "radio_freq_tuning_set", "WRITE AUTH REQUIRED"),
        ]
        catalog = generator.render()
        for kind, name, expected in cases:
            with self.subTest(kind=kind, name=name):
                self.assertEqual(rows[kind, name]["classification"], expected)
                prefix = "helper." if kind == "helper" else ""
                line = next(line for line in catalog.splitlines()
                            if line.startswith(f"- `{prefix}{name}(")
                            or line.startswith(f"- `{prefix}{name}`"))
                self.assertIn(f"**[{expected}]**", line)
        for row in rows.values():
            self.assertNotIn("destructive", row)

        args.json = False
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cli.cmd_methods(args, None)
        self.assertIn("[DEVICE READ]", output.getvalue())
        self.assertIn("[WRITE AUTH REQUIRED]", output.getvalue())
        self.assertNotIn("[DESTRUCTIVE]", output.getvalue() + catalog)

        original = cli.public_methods
        def future_get(self):
            """Unreviewed future method."""
        with mock.patch.object(
                cli, "public_methods",
                side_effect=lambda cls: original(cls) + [("future_get", future_get)]):
            args.json = True
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli.cmd_methods(args, None)
            future = [row for row in json.loads(output.getvalue())
                      if row["name"] == "future_get"]
            self.assertEqual(len(future), 2)
            self.assertTrue(all(row["classification"] == "WRITE AUTH REQUIRED"
                                for row in future))

        with mock.patch.object(
                generator.ra2cli, "public_methods",
                side_effect=lambda cls: original(cls) + [("future_get", future_get)]):
            future_lines = [line for line in generator.render().splitlines()
                            if line.startswith(("- `future_get(", "- `helper.future_get("))]
            self.assertEqual(len(future_lines), 2)
            self.assertTrue(all("[WRITE AUTH REQUIRED]" in line
                                for line in future_lines))

    def test_netconf_listing_and_catalog(self):
        if not all(importlib.util.find_spec(name)
                   for name in ("libyang", "ncclient", "lxml")):
            self.skipTest("NETCONF presentation checks require the shared .venv")
        cli = self.load(
            "skills/netconf/scripts/netconf_cli.py", "test_netconf_presentation")
        generator = self.load(
            "skills/netconf/scripts/gen_catalog.py", "test_netconf_catalog")
        args = argparse.Namespace(pattern=None)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cli.cmd_methods(args, None)
        listing = output.getvalue()
        catalog = generator.render()
        for name, expected in [
            ("set", "LOCAL ONLY"),
            ("update_from_dict", "LOCAL ONLY"),
            ("get_schema", "DEVICE READ"),
            ("get_config", "DEVICE READ"),
            ("set_config", "WRITE AUTH REQUIRED"),
        ]:
            with self.subTest(name=name):
                line = next(line for line in listing.splitlines()
                            if line.startswith(name + "("))
                self.assertIn(f"[{expected}]", line)
                line = next(line for line in catalog.splitlines()
                            if line.startswith(f"- `{name}("))
                self.assertIn(f"**[{expected}]**", line)
        self.assertNotIn("[DESTRUCTIVE]", listing + catalog)
        self.assertIn("mutate local XML only", catalog)

        def future_get(self):
            """Unreviewed future method."""
        with mock.patch.object(cli.NetconfDevice, "future_get", future_get, create=True):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli.cmd_methods(args, None)
            line = next(line for line in output.getvalue().splitlines()
                        if line.startswith("future_get("))
            self.assertIn("[WRITE AUTH REQUIRED]", line)
            line = next(line for line in generator.render().splitlines()
                        if line.startswith("- `future_get("))
            self.assertIn("**[WRITE AUTH REQUIRED]**", line)

    def test_catalogs_are_current_and_sources_use_lf(self):
        generators = [self.load(
            "skills/api/scripts/gen_catalog.py", "test_api_catalog_check")]
        if all(importlib.util.find_spec(name)
               for name in ("libyang", "ncclient", "lxml")):
            generators.append(self.load(
                "skills/netconf/scripts/gen_catalog.py", "test_netconf_catalog_check"))
        for generator in generators:
            self.assertEqual(generator.OUT.read_text(), generator.render())
            self.assertNotIn(b"\r", generator.OUT.read_bytes())
        for relative in [
            "scripts/racom_safety.py",
            "skills/api/scripts/ra2",
            "skills/api/scripts/gen_catalog.py",
            "skills/netconf/scripts/netconf_cli.py",
            "skills/netconf/scripts/gen_catalog.py",
            "tests/test_racom_safety.py",
        ]:
            self.assertNotIn(b"\r", (ROOT / relative).read_bytes(), relative)


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        # Wrap bootstrap errors: Python normally swallows sitecustomize errors.
        bootstrap = "try:\n" + "".join("    " + line + "\n" for line in BOOTSTRAP.splitlines())
        bootstrap += "except BaseException:\n    import os\n    os._exit(90)\n"
        (self.directory / "sitecustomize.py").write_text(bootstrap)
        (self.directory / ".env.ra2").write_text(
            "RA2_HOST=invalid\nRA2_USER=offline-test\nRACOM_ALLOW_WRITE=1\n")
        (self.directory / "params.json").write_text("{}")
        (self.directory / "binary.dat").write_bytes(b"\xff\x00")
        (self.directory / "sample.py").write_text("pass\n")
        (self.directory / "edit.xml").write_text("<config/>")
        (self.directory / "edit.json").write_text("{}")
        for name in ["get", "get-config", "edit-config", "copy-config", "delete-config",
                     "commit", "discard-changes", "lock", "unlock", "kill-session",
                     "close-session", "unknown"]:
            (self.directory / f"{name}.xml").write_text(f'<{name} xmlns="{NC}"/>')
        (self.directory / "get-schema.xml").write_text(f'<get-schema xmlns="{MON}"/>')

    def invoke(self, entry, argv, value):
        log = self.directory / (uuid.uuid4().hex + ".jsonl")
        log.write_text("")
        env = {key: val for key, val in os.environ.items()
               if not key.startswith(("RA2_", "RACOM_", "PYTHON"))
               and key not in {"NETCONF_PYTHON", "SCRIPTING_PYTHON"}}
        env.update({
            "PATH": str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", ""),
            "PYTHONPATH": str(self.directory), "PYTHONDONTWRITEBYTECODE": "1",
            "RACOM_TEST_ROOT": str(ROOT), "RACOM_TEST_EVENTS": str(log),
        })
        if value is not None:
            env["RACOM_ALLOW_WRITE"] = value
        result = subprocess.run([*entry, *argv], cwd=self.directory, env=env,
                                capture_output=True, text=True, timeout=30)
        events = [json.loads(line) for line in log.read_text().splitlines()]
        if "ready" not in events or "forbidden" in events:
            raise AssertionError(f"Offline boundary failure: {entry} {argv}: {result.stderr}")
        return result, events

    def check_case(self, case):
        tool, argv, entry, value, allowed = case
        result, events = self.invoke(entry, argv, value)
        expected = 0 if allowed else 5
        if result.returncode != expected:
            raise AssertionError(f"{tool} {argv} value={value!r} entry={entry}: "
                                 f"exit {result.returncode}, expected {expected}: {result.stderr}")
        if allowed:
            if events.count("dispatch") != 1:
                raise AssertionError(f"Missing/duplicate dispatch: {tool} {argv}")
        elif "dispatch" in events or "RACOM_ALLOW_WRITE must be exactly 1" not in result.stderr:
            raise AssertionError(f"Guard bypass or incorrect error: {tool} {argv}")

    def run_matrix(self, cases):
        with ThreadPoolExecutor(max_workers=16) as pool:
            for _ in pool.map(self.check_case, cases):
                pass
        print(f"  verified {len(cases)} offline subprocess cases", flush=True)

    def test_all_blocked_operations_and_environment_values(self):
        self.run_matrix([
            (tool, argv, entry, value, False)
            for tool, argv in blocked_cases() for entry in ENTRY[tool]
            for value in (None, "0", "", "true", "yes", "TRUE", "2", "01", " 1", "1 ")
        ])

    def test_exact_one_allows_protected_dispatch(self):
        self.run_matrix([
            (tool, argv, entry, "1", True)
            for tool, argv in blocked_cases() if "config_data=@missing.json" not in argv
            for entry in ENTRY[tool]
        ])

    def test_all_reads_remain_available(self):
        self.run_matrix([
            (tool, argv, entry, value, True)
            for tool, argv in allowed_cases() for entry in ENTRY[tool]
            for value in (None, "0", "1", "", "true", "yes", "TRUE", "2")
        ])

    def test_help_is_local(self):
        for entries in ENTRY.values():
            for entry in entries:
                result, events = self.invoke(entry, ["--help"], None)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("dispatch", events)

    def test_invalid_raw_xml_never_reaches_device(self):
        inputs = ["<", '<!DOCTYPE get SYSTEM "https://invalid/external.dtd">'
                  f'<get xmlns="{NC}"/>', '<!DOCTYPE get [<!ENTITY op "get">]>'
                  f'<get xmlns="{NC}">&op;</get>']
        for index, xml in enumerate(inputs):
            file = self.directory / f"invalid-{index}.xml"
            file.write_text(xml)
            for entry in ENTRY["netconf"]:
                for arg in (xml, f"@{file}"):
                    for value in (None, "0", "1"):
                        result, events = self.invoke(entry, ["rpc", arg], value)
                        self.assertEqual(result.returncode, 1, result.stderr)
                        self.assertNotIn("dispatch", events)

    def test_unknown_commands_default_deny(self):
        with mock.patch.dict(os.environ, {"RACOM_ALLOW_WRITE": "0"}):
            for tool in ("ra2", "netconf", "scripting", "future-tool"):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        racom_safety.guard_cli(tool, argparse.Namespace(cmd="future-command"))
                self.assertEqual(error.exception.code, 5)
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    racom_safety.guard_cli("netconf", argparse.Namespace(cmd="rpc"))
            self.assertEqual(error.exception.code, 5)

    def test_public_api_inventory_requires_review_when_library_changes(self):
        device = public_methods(ROOT / "skills/api/ra2_mgmt/src/rr_ra2_mgmt/device.py", "Device")
        helper = public_methods(ROOT / "skills/api/ra2_mgmt/src/rr_ra2_mgmt/helper.py", "Helper")
        self.assertEqual(device, (set(READ_RPC) | set(WRITE_RPC) | set(SESSION)) - DOCS_ONLY)
        self.assertEqual(helper, set(READ_HELPER) | set(WRITE_HELPER))

    def test_exact_error_message(self):
        result, events = self.invoke(ENTRY["ra2"][0],
                                    ["config", "set", "main.RR_StationName=sample"], None)
        self.assertEqual(result.returncode, 5)
        self.assertEqual(result.stderr,
                         "racom: blocked ra2 config set: device writes are disabled "
                         "(RACOM_ALLOW_WRITE must be exactly 1). "
                         "Enable it for one explicitly approved command only.\n")
        self.assertNotIn("dispatch", events)


if __name__ == "__main__":
    unittest.main()
