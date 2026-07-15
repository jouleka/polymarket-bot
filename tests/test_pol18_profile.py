import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "deploy" / "hermes" / "polymarket-profile" / "config.yaml"
APPROVED = {
    "propose_trade", "get_market", "get_book", "get_ledger", "get_flags",
}
MODEL_VISIBLE = {f"mcp__polymarket__{name}" for name in APPROVED}


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
    with pytest.raises(RuntimeError, match="effective MCP tools"):
        verify_effective_contract(
            config,
            hermes_version="0.18.2",
            mcp_version="1.26.0",
            platform_toolsets=platform_toolsets,
            discovered_mcp_tools={"polymarket": sorted(APPROVED - {"get_flags"})},
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

    config = json.loads(PROFILE.read_text(encoding="utf-8"))
    config["skills"]["inline_shell"] = True
    with pytest.raises(RuntimeError, match="skills"):
        verify_effective_contract(
            config,
            hermes_version="0.18.2",
            mcp_version="1.26.0",
            platform_toolsets=platform_toolsets,
            discovered_mcp_tools=discovered,
        )


def test_cron_contract_requires_one_exact_job_and_exact_model_visible_tools():
    from polybot.hermes.profile_verify import verify_cron_contract

    prompt = (ROOT / "deploy" / "hermes" / "polymarket-profile" /
              "cron-prompt.md").read_text(encoding="utf-8").rstrip("\n")
    job = {
        "name": "polymarket-propose-only",
        "prompt": prompt,
        "schedule": {"kind": "interval", "minutes": 5, "display": "every 5m"},
        "enabled": True,
        "skills": [],
        "skill": None,
        "model": None,
        "provider": None,
        "base_url": None,
        "script": None,
        "context_from": None,
        "enabled_toolsets": ["polymarket"],
        "workdir": None,
        "no_agent": False,
        "deliver": "local",
        "origin": None,
        "repeat": {"times": None, "completed": 7},
    }

    verify_cron_contract([job], prompt, sorted(MODEL_VISIBLE))

    unsafe = dict(job, enabled_toolsets=["terminal"])
    with pytest.raises(RuntimeError, match="cron job"):
        verify_cron_contract([unsafe], prompt, sorted(MODEL_VISIBLE))
    with pytest.raises(RuntimeError, match="exactly one"):
        verify_cron_contract([job, job], prompt, sorted(MODEL_VISIBLE))
    with pytest.raises(RuntimeError, match="model-visible"):
        verify_cron_contract([job], prompt, sorted(MODEL_VISIBLE | {"terminal"}))
    with pytest.raises(RuntimeError, match="model-visible"):
        verify_cron_contract(
            [job], prompt,
            sorted(MODEL_VISIBLE - {"mcp__polymarket__get_flags"}),
        )


def test_activation_requires_nonempty_owner_selected_model_and_provider():
    from polybot.hermes.profile_verify import verify_model_selection

    verify_model_selection({
        "model": {"default": "reviewed-model", "provider": "reviewed-provider"},
    })
    for model in (
        None,
        {},
        {"default": "", "provider": "reviewed-provider"},
        {"default": "reviewed-model", "provider": "  "},
        {"default": 7, "provider": "reviewed-provider"},
        {"default": "OWNER_CONFIG_REQUIRED", "provider": "reviewed-provider"},
    ):
        with pytest.raises(RuntimeError, match="model/provider"):
            verify_model_selection({"model": model})
