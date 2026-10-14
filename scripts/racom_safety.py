"""Fail-closed device-write policy for the repository's RACOM CLIs."""

import os
import sys

EXIT_WRITE_BLOCKED = 5
LOCAL_ONLY = "LOCAL ONLY"
DEVICE_READ = "DEVICE READ"
WRITE_AUTH_REQUIRED = "WRITE AUTH REQUIRED"

# Exact audited names. Never infer safety from a prefix or suffix.
READ_RPC = frozenset("""
events_get events_meta_get messages_get
settings_get settings_get_default settings_package_get
icmp_ping_poll
device_info_get_public device_info_get_private
device_capabilities_get device_constants_get device_ping
diagnostics_overview_get diagnostic_package_status_get
status_overview_data_get status_overview_card_get
status_info_data_get status_info_schema_get status_info_presentation_get
alarm_overview_data_get alarm_overview_card_get
statistics_meta_get statistics_data_get statistics_presentation_get
statistics_csv_get statistics_differential_data_get
monitoring_schema_get monitoring_live_poll_params_get monitoring_live_data_get
monitoring_config_get monitoring_config_get_default
monitoring_log_status_get monitoring_log_data_get
firmware_status_get firmware_module_status_get
file_distribution_status_send_get file_distribution_status_receive_get
file_repository_status_get
keyring_status_get keyring_secret_status_get keyring_secret_download
keyring_backup_get
users_get user_attributes_get users_backup_get
script_overview_get script_execution_get script_execution_output_get
script_execution_filter_options_get
log_stream_filter_options_get log_stream_poll rss_ping_poll ip_route_get
radio_freq_tuning_get
""".split())

READ_CALL = READ_RPC | {"connect", "close", "reconnect"}
READ_HELPER = frozenset("""
monitoring_live monitoring_log statistics statistics_differential status_info
""".split())

READ_NETCONF_RPC = frozenset({
    "{urn:ietf:params:xml:ns:netconf:base:1.0}get",
    "{urn:ietf:params:xml:ns:netconf:base:1.0}get-config",
    "{urn:ietf:params:xml:ns:yang:ietf-netconf-monitoring}get-schema",
})


LOCAL_NETCONF_MEMBERS = {
    "NetconfDevice": frozenset({"__init__"}),
    "NetconfConfig": frozenset({
        "__init__", "as_dict", "update_from_dict", "set", "get", "xml",
    }),
}

DEVICE_READ_NETCONF_MEMBERS = {
    "NetconfDevice": frozenset({
        "__enter__", "connect", "close", "reconnect",
        "get_config", "get_schema", "fetch_config_xml", "get_module_list",
    }),
    "NetconfConfig": frozenset({"from_device"}),
}


def classify_method(tool, context, name):
    """Classify exact audited methods; unknown tools/contexts/names fail closed."""
    if tool == "ra2":
        allowed = {
            "call": READ_CALL,
            "rpc": READ_RPC,
            "helper": READ_HELPER,
        }.get(context, frozenset())
        if name in allowed:
            return DEVICE_READ
    elif tool == "netconf":
        if name in LOCAL_NETCONF_MEMBERS.get(context, frozenset()):
            return LOCAL_ONLY
        if name in DEVICE_READ_NETCONF_MEMBERS.get(context, frozenset()):
            return DEVICE_READ
    return WRITE_AUTH_REQUIRED


def require_write(operation):
    if os.environ.get("RACOM_ALLOW_WRITE", "0") != "1":
        sys.stderr.write(
            f"racom: blocked {operation}: device writes are disabled "
            "(RACOM_ALLOW_WRITE must be exactly 1). "
            "Enable it for one explicitly approved command only.\n"
        )
        raise SystemExit(EXIT_WRITE_BLOCKED)


def guard_cli(tool, args, *, netconf_rpc_tag=None):
    """Authorize parsed operations before connection resolution or handlers."""
    command = args.cmd
    readonly = False
    operation = f"{tool} {command}"

    if tool == "ra2":
        if command in {"env", "methods", "doc", "ping", "status"}:
            readonly = True
        elif command == "config":
            operation += f" {args.sub}"
            readonly = args.sub in {"get", "keys", "meta"}
        elif command in {"call", "rpc"}:
            operation += f" {args.method}"
            readonly = classify_method(tool, command, args.method) != WRITE_AUTH_REQUIRED
        elif command == "helper":
            operation += f" {args.name}"
            readonly = classify_method(tool, command, args.name) != WRITE_AUTH_REQUIRED

    elif tool == "netconf":
        readonly = command in {
            "env", "methods", "doc", "ping", "modules", "schema",
            "tree", "find", "get", "leaf", "state",
        }
        if command == "rpc":
            readonly = netconf_rpc_tag in READ_NETCONF_RPC

    elif tool == "scripting":
        if command in {"env", "new", "check", "list", "logs", "executions"}:
            readonly = True
        elif command == "modules":
            readonly = not args.fetch
        elif command == "enable":
            readonly = args.on is None
        elif command == "run":
            readonly = args.no_start
        elif command in {"deploy", "undeploy"}:
            readonly = args.dry_run

    if not readonly:
        require_write(operation)
