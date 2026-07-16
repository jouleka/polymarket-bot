import json
from pathlib import Path
import signal
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "deploy" / "hermes" / "polymarket-profile" / "config.yaml"
APPROVED = {
    "propose_trade", "get_market", "get_book", "get_ledger", "get_flags",
}
MODEL_VISIBLE = {f"mcp__polymarket__{name}" for name in APPROVED}


def test_profile_verifier_targets_native_existing_hermes_profile():
    from polybot.hermes import profile_verify

    assert profile_verify._PROFILE_HOME == Path(
        "/root/.hermes/profiles/polymarket"
    )


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
    assert config["platform_toolsets"]["cron"] == []
    assert config["platform_toolsets"]["cli"] == []
    assert set(config["platforms"]) == set(config["platform_toolsets"]) - {
        "cli", "cron",
    }
    assert all(value == {"enabled": False}
               for value in config["platforms"].values())
    assert config["agent"]["disabled_toolsets"] == [
        "feishu_doc", "feishu_drive", "kanban",
    ]
    assert config["agent"]["reasoning_effort"] == "high"
    assert config["agent"]["restart_drain_timeout"] == 20
    assert config["kanban"] == {"dispatch_in_gateway": False}
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
    config["platform_toolsets"]["cron"] = ["polymarket"]
    with pytest.raises(RuntimeError, match="authored platform toolsets"):
        verify_effective_contract(
            config,
            hermes_version="0.18.2",
            mcp_version="1.26.0",
            platform_toolsets=platform_toolsets,
            discovered_mcp_tools=discovered,
        )

    config = json.loads(PROFILE.read_text(encoding="utf-8"))
    config["platforms"]["telegram"]["enabled"] = True
    with pytest.raises(RuntimeError, match="disable every messaging platform"):
        verify_effective_contract(
            config,
            hermes_version="0.18.2",
            mcp_version="1.26.0",
            platform_toolsets=platform_toolsets,
            discovered_mcp_tools=discovered,
        )

    config = json.loads(PROFILE.read_text(encoding="utf-8"))
    config["kanban"]["dispatch_in_gateway"] = True
    with pytest.raises(RuntimeError, match="kanban dispatcher"):
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


def test_profile_stop_marks_exact_profile_gateway_before_sigterm(
        monkeypatch, tmp_path):
    from polybot.hermes import profile_stop

    home = tmp_path / "polymarket"
    home.mkdir()
    (home / "config.yaml").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(profile_stop, "_PROFILE_HOME", home)
    events = []
    status = types.ModuleType("gateway.status")
    status.get_running_pid = lambda **kwargs: 4242
    status.write_planned_stop_marker = lambda pid: events.append(
        ("marker", pid)
    ) or True
    gateway = types.ModuleType("gateway")
    gateway.status = status
    monkeypatch.setitem(sys.modules, "gateway", gateway)
    monkeypatch.setitem(sys.modules, "gateway.status", status)
    monkeypatch.setattr(profile_stop.os, "kill", lambda pid, sig: events.append(
        ("kill", pid, sig)
    ))

    assert profile_stop.stop_installed_profile(home) is True
    assert events == [
        ("marker", 4242),
        ("kill", 4242, signal.SIGTERM),
    ]
    assert profile_stop.os.environ["HERMES_HOME"] == str(home)


def test_profile_stop_refuses_to_signal_without_planned_marker(
        monkeypatch, tmp_path):
    from polybot.hermes import profile_stop

    home = tmp_path / "polymarket"
    home.mkdir()
    (home / "config.yaml").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(profile_stop, "_PROFILE_HOME", home)
    status = types.ModuleType("gateway.status")
    status.get_running_pid = lambda **kwargs: 4242
    status.write_planned_stop_marker = lambda pid: False
    gateway = types.ModuleType("gateway")
    gateway.status = status
    monkeypatch.setitem(sys.modules, "gateway", gateway)
    monkeypatch.setitem(sys.modules, "gateway.status", status)
    monkeypatch.setattr(
        profile_stop.os, "kill",
        lambda pid, sig: pytest.fail("SIGTERM sent without planned marker"),
    )

    with pytest.raises(RuntimeError, match="mark Hermes gateway stop"):
        profile_stop.stop_installed_profile(home)


@pytest.mark.parametrize(("section", "field", "unsafe_value"), [
    ("approvals", "mode", "auto"),
    ("approvals", "cron_mode", "allow"),
    ("approvals", "mcp_reload_confirm", False),
    ("security", "allow_private_urls", True),
    ("security", "redact_secrets", False),
    ("security", "tirith_enabled", False),
    ("security", "tirith_fail_open", True),
    ("security", "allow_lazy_installs", True),
    (None, "hooks_auto_accept", True),
])
def test_effective_contract_rejects_each_unsafe_approval_and_security_setting(
        section, field, unsafe_value):
    from polybot.hermes.profile_verify import verify_effective_contract

    config = json.loads(PROFILE.read_text(encoding="utf-8"))
    if section is None:
        config[field] = unsafe_value
    else:
        config[section][field] = unsafe_value
    platform_toolsets = {
        platform: {"polymarket"} for platform in config["platform_toolsets"]
    }

    with pytest.raises(RuntimeError, match="approvals|security"):
        verify_effective_contract(
            config,
            hermes_version="0.18.2",
            mcp_version="1.26.0",
            platform_toolsets=platform_toolsets,
            discovered_mcp_tools={"polymarket": sorted(APPROVED)},
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
