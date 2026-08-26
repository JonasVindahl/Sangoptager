"""Niveaumeterets skala: hvilke dB-værdier farverne rent faktisk sidder på.

Skalaen er hele pointen med metret. Sidder rødt et andet sted end -6 dB,
lyver farven om, hvor tæt lyden er på at klippe — og så er den enten en
falsk alarm, man lærer at ignorere, eller en advarsel der aldrig kommer.
"""

import pytest

from sangoptager.ui import theme


def _level(db: float) -> float:
    """Lineært niveau 0..1 for en dBFS-værdi."""
    return 10 ** (db / 20)


def test_scale_is_in_db_not_amplitude():
    """0 dB yderst, bunden ved METER_FLOOR_DB, og lige langt mellem hver
    6 dB hele vejen — som på et studiemeter."""
    assert theme.meter_position_db(0.0) == 1.0
    assert theme.meter_position_db(-6.0) == pytest.approx(0.90)
    assert theme.meter_position_db(-12.0) == pytest.approx(0.80)
    assert theme.meter_position_db(-30.0) == pytest.approx(0.50)
    assert theme.meter_position_db(theme.METER_FLOOR_DB) == 0.0


def test_scale_clamps_outside_the_range():
    assert theme.meter_position_db(6.0) == 1.0        # over loftet
    assert theme.meter_position_db(-120.0) == 0.0     # under bunden
    assert theme.meter_position(0.0) == 0.0           # digital stilhed
    assert theme.meter_position(1.0) == 1.0


def test_linear_levels_land_where_their_db_value_says():
    for db in (-3.0, -6.0, -12.0, -18.0, -40.0):
        assert theme.meter_position(_level(db)) == pytest.approx(
            theme.meter_position_db(db))


def test_gradient_stops_follow_the_db_thresholds():
    """Positionerne er afledt af dB-tabellen, ikke skrevet i hånden — ellers
    skrider farverne, næste gang skalaens bund bliver justeret."""
    assert len(theme.METER_GRADIENT) == len(theme.METER_GRADIENT_DB)
    for (pos, colour), (db, db_colour) in zip(theme.METER_GRADIENT,
                                              theme.METER_GRADIENT_DB,
                                              strict=True):
        assert colour == db_colour
        assert pos == pytest.approx(theme.meter_position_db(db))


def test_red_means_close_to_the_ceiling_not_merely_loud():
    """Farverne aflæses på peak-stregen. En stemme med toppe på -12 dB er
    kraftig, men har stadig luft — først fra -6 dB er der grund til rødt."""
    red_at = theme.meter_position_db(-6.0)
    assert theme.meter_position(_level(-20.0)) < red_at
    assert theme.meter_position(_level(-12.0)) < red_at
    assert theme.meter_position(_level(-3.0)) > red_at
    assert theme.meter_position(_level(-0.5)) > red_at
