import json

import pytest

from security_response_generator import engagements


@pytest.fixture
def engagement_config(monkeypatch, tmp_path):
    root = tmp_path / "engagements"
    state = tmp_path / ".srg" / "active-engagement"
    monkeypatch.setattr(engagements.config, "ENGAGEMENTS_DIR", root)
    monkeypatch.setattr(engagements.config, "ACTIVE_ENGAGEMENT_PATH", state)

    demo = root / "demo"
    (demo / "customer_standards").mkdir(parents=True)
    (demo / "private_context").mkdir()
    (demo / "engagement.json").write_text(json.dumps({"customer_name": "DEMO"}), encoding="utf-8")
    return root, state


def test_defaults_to_demo_when_no_active_state_exists(engagement_config):
    engagement = engagements.active_engagement()

    assert engagement.slug == "demo"
    assert engagement.response_customer_name == "DEMO"


def test_create_engagement_builds_isolated_folders_and_activates_it(engagement_config):
    root, state = engagement_config

    engagement = engagements.create_engagement("virginia")

    assert engagement.customer_name == "Virginia"
    assert engagement.customer_standards_dir.is_dir()
    assert engagement.private_context_dir.is_dir()
    assert engagement.chroma_dir.is_dir()
    assert engagement.responses_dir.is_dir()
    assert state.read_text(encoding="utf-8").strip() == "virginia"
    assert engagement.root == root / "virginia"


def test_create_engagement_accepts_customer_display_name(engagement_config):
    engagement = engagements.create_engagement(
        "commonwealth-of-virginia",
        "Commonwealth of Virginia",
    )

    assert engagement.customer_name == "Commonwealth of Virginia"


def test_create_engagement_rejects_unsafe_names(engagement_config):
    with pytest.raises(ValueError):
        engagements.create_engagement("../another-customer")


def test_demo_name_is_reserved(engagement_config):
    with pytest.raises(ValueError, match="reserved"):
        engagements.create_engagement("demo")


def test_switching_engagement_changes_active_selection(engagement_config):
    engagements.create_engagement("virginia")
    engagements.create_engagement("acme-health")

    selected = engagements.set_active_engagement("virginia")

    assert selected.slug == "virginia"
    assert engagements.active_engagement().slug == "virginia"


def test_list_engagements_includes_demo_and_customer(engagement_config):
    engagements.create_engagement("virginia")

    found = engagements.list_engagements()

    assert [item.slug for item in found] == ["demo", "virginia"]
