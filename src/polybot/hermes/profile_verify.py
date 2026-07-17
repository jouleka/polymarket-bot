"""Fail-closed stopped preflight for the dedicated Hermes proposal profile."""

from __future__ import annotations

import argparse
import grp
import importlib.metadata
import os
from pathlib import Path
import stat

from polybot.hermes.rpc import APPROVED_METHODS


SUPPORTED_HERMES_VERSION = "0.18.2"
SUPPORTED_MCP_VERSION = "1.28.1"
MCP_SERVER_NAME = "polymarket"
PROFILE_PLATFORMS = frozenset({
    "api_server", "bluebubbles", "cli", "cron", "dingtalk", "discord",
    "email", "feishu", "google_chat", "homeassistant", "irc", "line",
    "matrix", "mattermost", "msgraph_webhook", "ntfy", "photon", "qqbot",
    "raft", "relay", "signal", "simplex", "slack", "sms", "teams",
    "telegram", "webhook", "wecom", "wecom_callback", "weixin", "whatsapp",
    "whatsapp_cloud", "yuanbao",
})
GATEWAY_PLATFORMS = PROFILE_PLATFORMS - {"cli", "cron"}
_BRIDGE_COMMAND = "/opt/polymarket-bot/.venv/bin/python"
_BRIDGE_ARGS = [
    "-m", "polybot.hermes.mcp_bridge", "--socket",
    "/run/polybot-proposal/proposal.sock",
]
_DISABLED_BUILTIN_TOOLSETS = ["feishu_doc", "feishu_drive", "kanban"]
_MODEL_VISIBLE_METHODS = {
    f"mcp__{MCP_SERVER_NAME}__{method}" for method in APPROVED_METHODS
}
_CRON_NAME = "polymarket-propose-only"
_CRON_SCHEDULE = {"kind": "interval", "minutes": 5, "display": "every 5m"}
_PROFILE_HOME = Path("/root/.hermes/profiles/polymarket")
_BRIDGE_GROUP = "polybot-proposal"
_CRON_PROMPT = Path(
    "/opt/polymarket-bot/deploy/hermes/polymarket-profile/cron-prompt.md"
)


def verify_effective_contract(config, *, hermes_version, mcp_version,
                              platform_toolsets, discovered_mcp_tools,
                              effective_gateway_platforms):
    """Verify authored config plus the toolsets/tools observed by Hermes itself."""
    if hermes_version != SUPPORTED_HERMES_VERSION:
        raise RuntimeError("unsupported Hermes version")
    if mcp_version != SUPPORTED_MCP_VERSION:
        raise RuntimeError("unsupported MCP SDK version")
    if not isinstance(config, dict) or set(config.get("mcp_servers", {})) != {
            MCP_SERVER_NAME}:
        raise RuntimeError("profile must configure exactly one MCP server")
    server = config["mcp_servers"][MCP_SERVER_NAME]
    expected_server_keys = {
        "command", "args", "env", "enabled", "timeout", "connect_timeout",
        "supports_parallel_tool_calls", "tools",
    }
    if not isinstance(server, dict) or set(server) != expected_server_keys:
        raise RuntimeError("polymarket MCP server keys violate the reviewed contract")
    if (server["command"] != _BRIDGE_COMMAND or server["args"] != _BRIDGE_ARGS
            or server["env"] != {"PYTHONPATH": "/opt/polymarket-bot/src"}
            or server["enabled"] is not True
            or server["supports_parallel_tool_calls"] is not False):
        raise RuntimeError("polymarket MCP transport violates the reviewed contract")
    if (isinstance(server["timeout"], bool) or not isinstance(server["timeout"], int)
            or not 1 <= server["timeout"] <= 10
            or isinstance(server["connect_timeout"], bool)
            or not isinstance(server["connect_timeout"], int)
            or not 1 <= server["connect_timeout"] <= 10):
        raise RuntimeError("polymarket MCP timeouts are outside the reviewed bounds")
    tools = server["tools"]
    include = tools.get("include") if isinstance(tools, dict) else None
    if (set(tools) != {"include", "resources", "prompts"}
            or not isinstance(include, list)
            or len(include) != len(set(include))
            or set(include) != APPROVED_METHODS
            or tools["resources"] is not False or tools["prompts"] is not False):
        raise RuntimeError("profile MCP tool grant is not exactly the approved five")

    authored_platforms = config.get("platform_toolsets")
    if (not isinstance(authored_platforms, dict)
            or set(authored_platforms) != PROFILE_PLATFORMS
            or any(value != [] for value in authored_platforms.values())):
        raise RuntimeError("authored platform toolsets are not MCP-only")
    gateway_platforms = config.get("platforms")
    if (not isinstance(gateway_platforms, dict)
            or set(gateway_platforms) != GATEWAY_PLATFORMS
            or any(value != {"enabled": False}
                   for value in gateway_platforms.values())):
        raise RuntimeError("profile must disable every messaging platform")
    agent = config.get("agent")
    if (not isinstance(agent, dict)
            or agent != {
                "disabled_toolsets": _DISABLED_BUILTIN_TOOLSETS,
                "reasoning_effort": "high",
                "restart_drain_timeout": 20,
            }):
        raise RuntimeError("authored disabled toolsets violate the reviewed contract")
    if config.get("kanban") != {"dispatch_in_gateway": False}:
        raise RuntimeError("profile kanban dispatcher must be disabled")
    verify_effective_gateway_contract(effective_gateway_platforms)
    if config.get("skills") != {
            "external_dirs": [], "inline_shell": False, "write_approval": False,
    }:
        raise RuntimeError("profile skills violate the reviewed contract")
    if config.get("approvals") != {
            "mode": "manual", "cron_mode": "deny", "mcp_reload_confirm": True,
    }:
        raise RuntimeError("profile approvals violate the reviewed contract")
    if config.get("security") != {
            "allow_private_urls": False,
            "redact_secrets": True,
            "tirith_enabled": True,
            "tirith_fail_open": False,
            "allow_lazy_installs": False,
    } or config.get("hooks_auto_accept") is not False:
        raise RuntimeError("profile security settings violate the reviewed contract")
    if (not isinstance(platform_toolsets, dict)
            or set(platform_toolsets) != PROFILE_PLATFORMS
            or any(set(value) != {MCP_SERVER_NAME}
                   for value in platform_toolsets.values())):
        raise RuntimeError("effective platform toolsets are not exactly MCP-only")

    if not isinstance(discovered_mcp_tools, dict) or set(discovered_mcp_tools) != {
            MCP_SERVER_NAME}:
        raise RuntimeError("effective MCP server inventory is not singular")
    observed = discovered_mcp_tools[MCP_SERVER_NAME]
    observed_names = [item[0] if isinstance(item, tuple) else item for item in observed]
    if (len(observed_names) != len(set(observed_names))
            or set(observed_names) != APPROVED_METHODS):
        raise RuntimeError("effective MCP tools are not exactly the approved five")
    return True


def verify_cron_contract(jobs, expected_prompt, model_visible_tool_names):
    """Pin the only scheduled agent and its final Hermes tool definitions."""
    if not isinstance(jobs, list) or len(jobs) != 1:
        raise RuntimeError("profile must contain exactly one cron job")
    job = jobs[0]
    expected = {
        "name": _CRON_NAME,
        "prompt": expected_prompt,
        "schedule": _CRON_SCHEDULE,
        "enabled": True,
        "skills": [],
        "skill": None,
        "model": None,
        "provider": None,
        "base_url": None,
        "script": None,
        "context_from": None,
        "enabled_toolsets": [MCP_SERVER_NAME],
        "workdir": None,
        "no_agent": False,
        "deliver": "local",
        "origin": None,
    }
    if (not isinstance(job, dict)
            or any(job.get(key) != value for key, value in expected.items())
            or job.get("attach_to_session") not in (None, False)):
        raise RuntimeError("cron job violates the reviewed propose-only contract")
    repeat = job.get("repeat")
    if (not isinstance(repeat, dict) or set(repeat) != {"times", "completed"}
            or repeat["times"] is not None
            or isinstance(repeat["completed"], bool)
            or not isinstance(repeat["completed"], int)
            or repeat["completed"] < 0):
        raise RuntimeError("cron job repeat state violates the reviewed contract")
    names = list(model_visible_tool_names)
    if len(names) != len(set(names)) or set(names) != _MODEL_VISIBLE_METHODS:
        raise RuntimeError("cron model-visible tools are not exactly the approved five")
    return True


def verify_effective_gateway_contract(platforms):
    """Require the pinned Hermes gateway inventory to contain zero adapters."""
    if (not isinstance(platforms, dict)
            or set(platforms) != GATEWAY_PLATFORMS
            or any(value is not False for value in platforms.values())):
        raise RuntimeError("effective Hermes gateway is not cron-only")
    return True


def _verify_model_visible_tools(names):
    names = list(names)
    if len(names) != len(set(names)) or set(names) != _MODEL_VISIBLE_METHODS:
        raise RuntimeError("cron model-visible tools are not exactly the approved five")


def _verify_profile_filesystem(home):
    if home != _PROFILE_HOME:
        raise RuntimeError("Hermes profile path is not the reviewed isolated path")
    home_stat = home.lstat()
    config_stat = (home / "config.yaml").lstat()
    if (not stat.S_ISDIR(home_stat.st_mode)
            or stat.S_IMODE(home_stat.st_mode) & 0o022
            or home_stat.st_uid != os.geteuid()
            or not stat.S_ISREG(config_stat.st_mode)
            or stat.S_IMODE(config_stat.st_mode) != 0o600
            or config_stat.st_uid != os.geteuid()
            or config_stat.st_gid != os.getegid()):
        raise RuntimeError("Hermes profile ownership or mode is unsafe")
    try:
        bridge_gid = grp.getgrnam(_BRIDGE_GROUP).gr_gid
    except KeyError as exc:
        raise RuntimeError("proposal bridge group is unavailable") from exc
    effective_groups = set(os.getgroups()) | {os.getegid()}
    if effective_groups != {os.getegid(), bridge_gid}:
        raise RuntimeError("Hermes process group membership is unsafe")
    _verify_no_local_profile_secrets(home)


def _verify_no_local_profile_secrets(home):
    if any((home / name).exists() for name in (".env", ".op.env", "auth.json")):
        raise RuntimeError("Hermes profile must use only the native root auth store")


def verify_model_selection(config):
    model = config.get("model") if isinstance(config, dict) else None
    if not isinstance(model, dict):
        raise RuntimeError("profile model/provider requires owner selection")
    for field in ("default", "provider"):
        value = model.get(field)
        if (not isinstance(value, str) or not value.strip()
                or value == "OWNER_CONFIG_REQUIRED"):
            raise RuntimeError("profile model/provider requires owner selection")
    return True


def verify_installed_profile(profile_home, *, expect_no_cron=False):
    """Run inside the pinned Hermes venv; discovery starts only the stdio bridge."""
    home = Path(profile_home)
    if not home.is_absolute() or not (home / "config.yaml").is_file():
        raise RuntimeError("Hermes profile home must contain config.yaml")
    _verify_profile_filesystem(home)
    os.environ["HERMES_HOME"] = str(home)

    # Imports are deliberately late: Hermes caches HERMES_HOME in module globals.
    from hermes_cli.config import read_raw_config
    from hermes_cli.tools_config import _get_platform_tools
    from cron.jobs import list_jobs
    from gateway.config import load_gateway_config
    from model_tools import get_tool_definitions
    from tools.mcp_tool import (
        discover_mcp_tools, probe_mcp_server_tools, shutdown_mcp_servers,
    )

    config = read_raw_config()
    verify_model_selection(config)
    platform_toolsets = {
        platform: _get_platform_tools(config, platform)
        for platform in PROFILE_PLATFORMS
    }
    gateway = load_gateway_config()
    effective_gateway_platforms = {
        platform.value: item.enabled
        for platform, item in gateway.platforms.items()
    }
    verify_effective_contract(
        config,
        hermes_version=importlib.metadata.version("hermes-agent"),
        mcp_version=importlib.metadata.version("mcp"),
        platform_toolsets=platform_toolsets,
        discovered_mcp_tools=probe_mcp_server_tools(),
        effective_gateway_platforms=effective_gateway_platforms,
    )
    jobs = list_jobs(include_disabled=True)
    if expect_no_cron:
        if jobs:
            raise RuntimeError("pre-cron profile unexpectedly contains cron state")
        enabled_toolsets = [MCP_SERVER_NAME]
    else:
        if not isinstance(jobs, list) or len(jobs) != 1:
            raise RuntimeError("profile must contain exactly one cron job")
        enabled_toolsets = jobs[0].get("enabled_toolsets")
    try:
        discover_mcp_tools()
        definitions = get_tool_definitions(
            enabled_toolsets=enabled_toolsets,
            disabled_toolsets=_DISABLED_BUILTIN_TOOLSETS,
            quiet_mode=True,
        )
        model_names = [item["function"]["name"] for item in definitions]
    finally:
        shutdown_mcp_servers()
    if expect_no_cron:
        _verify_model_visible_tools(model_names)
    else:
        expected_prompt = _CRON_PROMPT.read_text(encoding="utf-8").rstrip("\n")
        verify_cron_contract(jobs, expected_prompt, model_names)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(prog="verify-polymarket-hermes")
    parser.add_argument("--profile-home", required=True)
    parser.add_argument("--expect-no-cron", action="store_true")
    args = parser.parse_args(argv)
    verify_installed_profile(args.profile_home, expect_no_cron=args.expect_no_cron)
    print("POL-18 Hermes profile effective inventory: exact five; PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
