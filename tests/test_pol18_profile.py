import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "deploy" / "hermes" / "polymarket-profile" / "config.yaml"
APPROVED = {
    "propose_trade", "get_market", "get_book", "get_ledger", "get_flags",
}


def test_profile_template_grants_only_one_exact_five_tool_mcp_server():
    config = json.loads(PROFILE.read_text(encoding="utf-8"))

    assert set(config["mcp_servers"]) == {"polymarket"}
    server = config["mcp_servers"]["polymarket"]
    assert server["command"] == "/opt/polymarket-bot/.venv/bin/python"
    assert server["args"] == [
        "-m", "polybot.hermes.mcp_bridge", "--socket",
        "/run/polybot-proposal/proposal.sock",
    ]
    assert set(server["tools"]["include"]) == APPROVED
    assert len(server["tools"]["include"]) == len(APPROVED)
    assert server["tools"]["resources"] is False
    assert server["tools"]["prompts"] is False
    assert config["platform_toolsets"]["cron"] == ["polymarket"]
    assert config["platform_toolsets"]["cli"] == ["polymarket"]
    assert config["agent"]["disabled_toolsets"] == [
        "feishu_doc", "feishu_drive", "kanban",
    ]
    assert config["model"]["default"] == "OWNER_CONFIG_REQUIRED"
    assert config["model"]["provider"] == "OWNER_CONFIG_REQUIRED"


def test_effective_inventory_verifier_rejects_any_extra_tool_or_toolset():
    from polybot.hermes.profile_verify import verify_effective_contract

    config = json.loads(PROFILE.read_text(encoding="utf-8"))
    platform_toolsets = {
        platform: {"polymarket"} for platform in config["platform_toolsets"]
    }
    discovered = {"polymarket": sorted(APPROVED)}

    verify_effective_contract(
        config,
        hermes_version="0.18.2",
        mcp_version="1.26.0",
        platform_toolsets=platform_toolsets,
        discovered_mcp_tools=discovered,
    )

    with pytest.raises(RuntimeError, match="effective MCP tools"):
        verify_effective_contract(
            config,
            hermes_version="0.18.2",
            mcp_version="1.26.0",
            platform_toolsets=platform_toolsets,
            discovered_mcp_tools={"polymarket": sorted(APPROVED | {"terminal"})},
        )
    with pytest.raises(RuntimeError, match="platform toolsets"):
        verify_effective_contract(
            config,
            hermes_version="0.18.2",
            mcp_version="1.26.0",
            platform_toolsets=platform_toolsets | {"cron": {"polymarket", "terminal"}},
            discovered_mcp_tools=discovered,
        )

    config["agent"]["disabled_toolsets"] = []
    with pytest.raises(RuntimeError, match="disabled toolsets"):
        verify_effective_contract(
            config,
            hermes_version="0.18.2",
            mcp_version="1.26.0",
            platform_toolsets=platform_toolsets,
            discovered_mcp_tools=discovered,
        )
