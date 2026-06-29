"""PaperSigner S4.2 de-risk primitives: cancel_all / place_gtd_bracket / run_canary, with the
shadow-record lists cancelled_all / gtd_exits. The load-bearing safety property (DESIGN §9):
cancel_all cancels WORKING ENTRY orders but the GTD EXIT brackets SURVIVE -- they are the
passive backstop on a wedge."""
from decimal import Decimal

from polybot.ers.signer import Signer
from polybot.ers.service import PaperSigner
from polybot.ers.validator import OpenPosition


def _pos(token="A", entry="0.50", risk="12"):
    return OpenPosition("m", "e", "s", "c", Decimal(risk), False,
                        token_id=token, entry_price=Decimal(entry), frozen=False)


def test_paper_signer_is_a_structural_signer():
    # Extending it with the three new methods makes it satisfy the Protocol.
    assert isinstance(PaperSigner(), Signer)


def test_new_recorder_lists_start_empty():
    s = PaperSigner()
    assert s.cancelled_all == []
    assert s.gtd_exits == []


def test_place_gtd_bracket_records_the_standing_exit():
    s = PaperSigner()
    s.place_gtd_bracket(_pos(token="A", risk="12"), exit_price=Decimal("0.10"), expiry=1700)
    assert s.gtd_exits == [
        {"token_id": "A", "exit_price": Decimal("0.10"), "expiry": 1700, "size": Decimal("12")}
    ]


def test_cancel_all_records_a_marker():
    s = PaperSigner()
    s.cancel_all()
    assert len(s.cancelled_all) == 1


def test_cancel_all_keeps_the_gtd_exit_brackets():
    # DESIGN §9: cancel_all cancels WORKING ENTRY orders, NOT the protective GTD exits.
    s = PaperSigner()
    s.place_gtd_bracket(_pos(token="A", risk="12"), exit_price=Decimal("0.10"), expiry=1700)
    s.place_gtd_bracket(_pos(token="B", risk="8"), exit_price=Decimal("0.05"), expiry=1800)
    before = list(s.gtd_exits)
    s.cancel_all()
    assert s.gtd_exits == before, "cancel_all must NOT clear the protective GTD exit brackets"
    assert len(s.gtd_exits) == 2


def test_run_canary_returns_true_in_shadow_and_records_nothing_extra():
    # Shadow: returns True (real sign+place+cancel is POL-4). NEVER blind-retries.
    s = PaperSigner()
    assert s.run_canary() is True
    assert s.placed == [] and s.gtd_exits == []
