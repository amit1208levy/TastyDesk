"""The catalogue has to explain itself.

The page no longer carries notes in its margins: hovering a heading for two
seconds is the only place a field's meaning is written down. That makes a
missing explanation invisible rather than obvious, so it is asserted here.
"""

from tastydesk.core.indicators import _LEG_HELP, _STRATEGY_HELP, LEG_FIELDS, STRATEGY_FIELDS


def test_every_field_is_explained() -> None:
    for field in STRATEGY_FIELDS + LEG_FIELDS:
        assert field.help, f"{field.id} has no explanation"
        # A sentence, not a second label: the hint is already the short form.
        assert len(field.help) > len(field.hint), field.id
        assert field.help.strip().endswith("."), field.id


def test_no_explanation_without_a_field() -> None:
    """A renamed field leaves its paragraph behind; this catches that."""
    assert set(_STRATEGY_HELP) == {f.id for f in STRATEGY_FIELDS}
    assert set(_LEG_HELP) == {f.id for f in LEG_FIELDS}


def test_short_delta_says_what_it_means() -> None:
    """The one the old margin note explained, kept as a worked example."""
    field = next(f for f in STRATEGY_FIELDS if f.id == "short_delta")
    assert "in the money" in field.help
    assert "0.30" in field.help
