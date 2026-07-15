"""Fail-closed stopped preflight for the dedicated Hermes proposal profile."""

from __future__ import annotations

import argparse
import importlib.metadata
import os
from pathlib import Path

from polybot.hermes.rpc import APPROVED_METHODS


SUPPORTED_HERMES_VERSION = "0.18.2"
SUPPORTED_MCP_VERSION = "1.26.0"
MCP_SERVER_NAME = "polymarket"
PROFILE_PLATFORMS = frozenset({
    "api_server", "bluebubbles", "cli", "cron", "dingtalk", "discord",
    "email", "feishu", "homeassistant", "matrix", "mattermost", "qqbot",
    "signal", "slack", "telegram", "webhook", "wecom", "wecom_callback",
    "weixin", "whatsapp", "whatsapp_cloud", "yuanbao",
})
_BRIDGE_COMMAND = "/opt/polymarket-bot/.venv/bin/python"
_BRIDGE_ARGS = [
    "-m", "polybot.hermes.mcp_bridge", "--socket",
    "/run/polybot-proposal/proposal.sock",
]


def verify_effective_contract(config, *, hermes_version, mcp_version,
                              platform_toolsets, discovered_mcp_tools):
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
            or any(value != [MCP_SERVER_NAME] for value in authored_platforms.values())):
        raise RuntimeError("authored platform toolsets are not MCP-only")
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


def verify_installed_profile(profile_home):
    """Run inside the pinned Hermes venv; discovery starts only the stdio bridge."""
    home = Path(profile_home)
    if not home.is_absolute() or not (home / "config.yaml").is_file():
        raise RuntimeError("Hermes profile home must contain config.yaml")
    os.environ["HERMES_HOME"] = str(home)

    # Imports are deliberately late: Hermes caches HERMES_HOME in module globals.
    from hermes_cli.config import read_raw_config
    from hermes_cli.tools_config import _get_platform_tools
    from tools.mcp_tool import probe_mcp_server_tools

    config = read_raw_config()
    model = config.get("model") if isinstance(config, dict) else None
    if (not isinstance(model, dict)
            or model.get("default") == "OWNER_CONFIG_REQUIRED"
            or model.get("provider") == "OWNER_CONFIG_REQUIRED"):
        raise RuntimeError("profile model/provider requires an owner-approved stopped configuration")
    platform_toolsets = {
        platform: _get_platform_tools(config, platform)
        for platform in PROFILE_PLATFORMS
    }
    return verify_effective_contract(
        config,
        hermes_version=importlib.metadata.version("hermes-agent"),
        mcp_version=importlib.metadata.version("mcp"),
        platform_toolsets=platform_toolsets,
        discovered_mcp_tools=probe_mcp_server_tools(),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(prog="verify-polymarket-hermes")
    parser.add_argument("--profile-home", required=True)
    args = parser.parse_args(argv)
    verify_installed_profile(args.profile_home)
    print("POL-18 Hermes profile effective inventory: exact five; PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
