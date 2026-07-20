"""S6 / POL-8 — deploy/hermes/config.yaml reviewed artifact (Task 10).

Safety property under test: the Hermes harness is granted EXACTLY the five
read tools plus the single INSERT-only write tool `propose_trade`, and is
granted NONE of the signing/admin/status-mutation tools that would let it
reach the signer or flip an intent's status. The artifact also documents the
deployment posture (own Linux user, no keys, no shell into the ERS, may
rewrite only its own SKILL.md).

This repo has NO YAML dependency (pyproject deps = httpx, websockets only),
so the test is stdlib-only: a tiny purpose-built parser extracts the
`tools.include` list. No `import yaml`.
"""

from pathlib import Path

# Repo-root-relative path to the reviewed artifact (this test file lives in
# <repo>/tests/, so the repo root is its parent's parent).
_CONFIG_PATH = Path(__file__).resolve().parents[1] / "deploy" / "hermes" / "config.yaml"

# The pinned contract: the complete allowed tool surface.
_ALLOWED_TOOLS = frozenset(
    {"propose_trade", "get_market", "get_book", "get_news", "get_ledger", "get_flags"}
)

# Tools that MUST never appear — anything that signs, moves money, mutates an
# intent's status, or reaches the chokepoint mutators on IntentStore.
_DENYLIST = frozenset(
    {
        "place",
        "flatten",
        "record_decision",
        "pending",
        "sign",
        "signer",
        "place_order",
        "cancel_order",
        "transfer",
        "withdraw",
        "approve",
        "admin",
        "update_status",
        "set_status",
    }
)


def _parse_tools_include(text):
    """Stdlib-only YAML-subset parser: return the list items nested under
    `tools:` -> `include:`. Tolerates inline `# comments`. Requires the
    artifact to use the simple block-list shape (one `- name` per line)."""
    lines = text.splitlines()
    in_tools = False
    in_include = False
    items = []
    for raw in lines:
        # Strip trailing comments and trailing whitespace; keep leading indent.
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            # A new top-level key ends any block we were inside.
            in_tools = stripped.startswith("tools:")
            in_include = False
            continue
        if in_tools and stripped.startswith("include:") and not in_include:
            in_include = True
            continue
        if in_include:
            if stripped.startswith("- "):
                items.append(stripped[2:].strip().strip("'\""))
            elif indent <= 2 and not stripped.startswith("- "):
                # Dedent back to a sibling key under `tools:` -> include block done.
                in_include = False
    return items


def _parse_block_keys(text, block_name):
    """Stdlib-only: return {key: value-token} for the simple `key: value`
    lines nested one level under a top-level `block_name:` mapping. Folded
    (`>`) and nested blocks are skipped (only scalar `key: token` lines)."""
    lines = text.splitlines()
    in_block = False
    out = {}
    for raw in lines:
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            in_block = stripped.startswith(f"{block_name}:")
            continue
        if in_block and indent == 2 and ":" in stripped:
            key, _, val = stripped.partition(":")
            out[key.strip()] = val.strip()
    return out


def test_config_artifact_exists():
    assert _CONFIG_PATH.is_file(), f"missing reviewed artifact: {_CONFIG_PATH}"


def test_tools_include_is_exactly_the_allowed_set():
    text = _CONFIG_PATH.read_text(encoding="utf-8")
    items = _parse_tools_include(text)
    assert items, "tools.include parsed empty — artifact shape changed"
    # No duplicates, and the set matches the pinned contract exactly.
    assert len(items) == len(set(items)), f"duplicate tool entries: {items}"
    assert set(items) == _ALLOWED_TOOLS, f"tools.include != allowed set: {sorted(items)}"


def test_tools_include_grants_no_signing_or_admin_tool():
    text = _CONFIG_PATH.read_text(encoding="utf-8")
    items = set(_parse_tools_include(text))
    leaked = items & _DENYLIST
    assert not leaked, f"forbidden tool(s) granted to Hermes: {sorted(leaked)}"


def test_deployment_posture_is_documented_and_locked():
    text = _CONFIG_PATH.read_text(encoding="utf-8")
    posture = _parse_block_keys(text, "deployment")
    assert posture.get("own_linux_user") == "true", posture
    assert posture.get("holds_keys") == "false", posture
    assert posture.get("shell_into_ers") == "false", posture
    assert posture.get("may_rewrite_skill_md_only") == "true", posture
    assert posture.get("secrets_in_model_mutable_text") == "false", posture
