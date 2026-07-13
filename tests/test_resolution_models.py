"""POL-15 pure resolution authority models."""

import pytest

from polybot.resolution.models import ResolutionSubject


_CONDITION = "0x" + "11" * 32


def test_resolution_subject_requires_exact_binary_identity():
    subject = ResolutionSubject(
        event_id="event-1",
        condition_id=_CONDITION,
        token_ids=("101", "202"),
        category="politics",
    )
    assert subject.token_ids == ("101", "202")

    invalid = (
        {"event_id": ""},
        {"event_id": " event-1"},
        {"condition_id": "0x" + "AA" * 32},
        {"condition_id": "0x11"},
        {"token_ids": ("101",)},
        {"token_ids": ("101", "101")},
        {"token_ids": ("01", "202")},
        {"token_ids": ("0", "202")},
        {"category": ""},
    )
    base = dict(event_id="event-1", condition_id=_CONDITION,
                token_ids=("101", "202"), category="politics")
    for change in invalid:
        with pytest.raises((TypeError, ValueError)):
            ResolutionSubject(**(base | change))
