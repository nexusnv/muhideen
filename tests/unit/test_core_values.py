"""Guards for core value objects (slice 1A-1, Task 1)."""

from dataclasses import FrozenInstanceError
from datetime import date, datetime, time

import pytest

from muhideen.core.values import (
    IqamahRule,
    MarkerKind,
    MarkerName,
    NextEvent,
    PrayerDay,
    PrayerState,
    ScheduleSource,
    Settings,
    marker_kind,
)


def _day() -> PrayerDay:
    return PrayerDay(
        date=date(2025, 10, 20),
        zone="SGR01",
        imsak=time(5, 35),
        fajr=time(5, 45),
        syuruq=time(6, 55),
        dhuha=time(7, 25),
        dhuhr=time(12, 15),
        asr=time(15, 30),
        maghrib=time(18, 5),
        isha=time(19, 25),
        source=ScheduleSource.JAKIM,
        fetched_at=datetime(2025, 10, 19, 2, 0),
    )


def _event() -> NextEvent:
    return NextEvent(
        now=datetime(2025, 10, 20, 11, 45),
        state=PrayerState.IQAMAH_COUNTDOWN,
        next_prayer=MarkerName.DHUHR,
        adhan_at=datetime(2025, 10, 20, 12, 15),
        iqamah_at=datetime(2025, 10, 20, 12, 30),
        dim_until=datetime(2025, 10, 20, 12, 50),
        stale=False,
        next_boundary=None,
        boundary_at=None,
    )


@pytest.mark.unit
def test_prayer_day_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        _day().zone = "WKP01"  # type: ignore[misc]


@pytest.mark.unit
def test_next_event_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        _event().stale = True  # type: ignore[misc]


@pytest.mark.unit
def test_iqamah_rule_and_settings_frozen() -> None:
    rule = IqamahRule(prayer=MarkerName.FAJR, mode="delay", delay_minutes=15)
    settings = Settings(masjid_name="M", zone="SGR01", hijri_offset=0)
    with pytest.raises(FrozenInstanceError):
        rule.delay_minutes = 10  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        settings.zone = "WKP01"  # type: ignore[misc]


@pytest.mark.unit
def test_value_equality_and_hash() -> None:
    assert _day() == _day()
    assert hash(_day()) == hash(_day())
    other = PrayerDay(
        date=date(2025, 10, 20),
        zone="WKP01",
        imsak=time(5, 35),
        fajr=time(5, 45),
        syuruq=time(6, 55),
        dhuha=time(7, 25),
        dhuhr=time(12, 15),
        asr=time(15, 30),
        maghrib=time(18, 5),
        isha=time(19, 25),
        source=ScheduleSource.JAKIM,
        fetched_at=datetime(2025, 10, 19, 2, 0),
    )
    assert _day() != other


@pytest.mark.unit
def test_next_event_equality_and_hash() -> None:
    assert _event() == _event()
    assert hash(_event()) == hash(_event())
    other = NextEvent(
        now=datetime(2025, 10, 20, 11, 45),
        state=PrayerState.NORMAL,
        next_prayer=MarkerName.DHUHR,
        adhan_at=datetime(2025, 10, 20, 12, 15),
        iqamah_at=datetime(2025, 10, 20, 12, 30),
        dim_until=datetime(2025, 10, 20, 12, 50),
        stale=False,
        next_boundary=None,
        boundary_at=None,
    )
    assert _event() != other


@pytest.mark.unit
def test_iqamah_rule_equality_and_hash() -> None:
    rule = IqamahRule(prayer=MarkerName.FAJR, mode="delay", delay_minutes=15)
    twin = IqamahRule(prayer=MarkerName.FAJR, mode="delay", delay_minutes=15)
    other = IqamahRule(prayer=MarkerName.FAJR, mode="delay", delay_minutes=20)
    assert rule == twin
    assert hash(rule) == hash(twin)
    assert rule != other


@pytest.mark.unit
def test_settings_equality_and_hash() -> None:
    settings = Settings(masjid_name="M", zone="SGR01", hijri_offset=0)
    twin = Settings(masjid_name="M", zone="SGR01", hijri_offset=0)
    other = Settings(masjid_name="M", zone="WKP01", hijri_offset=0)
    assert settings == twin
    assert hash(settings) == hash(twin)
    assert settings != other


@pytest.mark.unit
@pytest.mark.parametrize("offset", [-3, -2, -1, 0, 1, 2, 3])
def test_settings_hijri_offset_range(offset: int) -> None:
    if -2 <= offset <= 2:
        assert Settings(masjid_name="M", zone="SGR01", hijri_offset=offset)
    else:
        with pytest.raises(ValueError, match="hijri_offset out of range"):
            Settings(masjid_name="M", zone="SGR01", hijri_offset=offset)


@pytest.mark.unit
@pytest.mark.parametrize("delay", [-1, 0, 1, 60, 61])
def test_iqamah_rule_delay_minutes_range_0_to_60(delay: int) -> None:
    if 0 <= delay <= 60:
        rule = IqamahRule(prayer=MarkerName.DHUHR, mode="delay", delay_minutes=delay)
        assert rule.delay_minutes == delay
    else:
        with pytest.raises(ValueError, match="delay_minutes out of range"):
            IqamahRule(prayer=MarkerName.DHUHR, mode="delay", delay_minutes=delay)


@pytest.mark.unit
def test_settings_default_iqamah_rules_match_prd_fr_1_4() -> None:
    from muhideen.core import DEFAULT_IQAMAH_RULES

    settings = Settings(masjid_name="M", zone="SGR01", hijri_offset=0)
    by_prayer = {rule.prayer: rule for rule in settings.iqamah_rules}
    assert set(by_prayer) == {
        MarkerName.FAJR,
        MarkerName.DHUHR,
        MarkerName.ASR,
        MarkerName.MAGHRIB,
        MarkerName.ISHA,
        MarkerName.JUMUAH,
    }
    assert by_prayer[MarkerName.FAJR].delay_minutes == 15
    assert by_prayer[MarkerName.DHUHR].delay_minutes == 10
    assert by_prayer[MarkerName.ASR].delay_minutes == 10
    assert by_prayer[MarkerName.MAGHRIB].delay_minutes == 10
    assert by_prayer[MarkerName.ISHA].delay_minutes == 15
    assert by_prayer[MarkerName.JUMUAH].delay_minutes == 10
    assert all(rule.mode == "delay" for rule in settings.iqamah_rules)
    assert MarkerName.SYURUQ not in by_prayer
    assert not (
        {MarkerName.IMSAK, MarkerName.SYURUQ, MarkerName.DHUHA} & set(by_prayer)
    )
    assert settings.iqamah_rules == DEFAULT_IQAMAH_RULES


@pytest.mark.unit
def test_settings_calc_config_defaults_disabled() -> None:
    settings = Settings(masjid_name="M", zone="SGR01", hijri_offset=0)
    assert settings.lat is None
    assert settings.lon is None
    assert settings.method == "MABIMS"


@pytest.mark.unit
@pytest.mark.parametrize(("lat", "lon"), [(3.1, None), (None, 101.6)])
def test_settings_rejects_half_configured_coordinates(
    lat: float | None, lon: float | None
) -> None:
    with pytest.raises(ValueError, match="lat and lon must be set together"):
        Settings(masjid_name="M", zone="SGR01", hijri_offset=0, lat=lat, lon=lon)


@pytest.mark.unit
def test_settings_rejects_out_of_range_coordinates() -> None:
    with pytest.raises(ValueError, match="latitude out of range"):
        Settings(masjid_name="M", zone="SGR01", hijri_offset=0, lat=91.0, lon=101.6)
    with pytest.raises(ValueError, match="longitude out of range"):
        Settings(masjid_name="M", zone="SGR01", hijri_offset=0, lat=3.1, lon=181.0)


@pytest.mark.unit
def test_settings_new_fields_frozen_hashable_and_compared() -> None:
    settings = Settings(masjid_name="M", zone="SGR01", hijri_offset=0)
    with pytest.raises(FrozenInstanceError):
        settings.method = "MWL"  # type: ignore[misc]
    assert isinstance(hash(settings), int)
    other = Settings(masjid_name="M", zone="SGR01", hijri_offset=0, method="MWL")
    assert settings != other


@pytest.mark.unit
def test_prayer_state_members() -> None:
    assert {s.name for s in PrayerState} == {
        "NORMAL",
        "PRE_ADHAN",
        "ADHAN",
        "IQAMAH_COUNTDOWN",
        "SALAH_DIM",
    }


@pytest.mark.unit
def test_marker_name_members() -> None:
    assert {p.value for p in MarkerName} == {
        "fajr",
        "imsak",
        "syuruq",
        "dhuha",
        "dhuhr",
        "asr",
        "maghrib",
        "isha",
        "jumuah",
    }
    # Locked wire spellings: pre-existing names keep their contract strings.
    assert MarkerName.DHUHR.value == "dhuhr"
    assert MarkerName.SYURUQ.value == "syuruq"


@pytest.mark.unit
def test_marker_kind_classification() -> None:
    from muhideen.core import MarkerKind as CoreMarkerKind
    from muhideen.core import MarkerName as CoreMarkerName
    from muhideen.core import marker_kind as core_marker_kind

    assert CoreMarkerKind is MarkerKind
    assert CoreMarkerName is MarkerName
    assert core_marker_kind is marker_kind
    prayers = {
        MarkerName.FAJR,
        MarkerName.DHUHR,
        MarkerName.ASR,
        MarkerName.MAGHRIB,
        MarkerName.ISHA,
        MarkerName.JUMUAH,
    }
    boundaries = {MarkerName.IMSAK, MarkerName.SYURUQ, MarkerName.DHUHA}
    assert len(set(MarkerName)) == 9
    assert prayers | boundaries == set(MarkerName)
    assert all(marker_kind(name) is MarkerKind.PRAYER for name in prayers)
    assert all(marker_kind(name) is MarkerKind.BOUNDARY for name in boundaries)


@pytest.mark.unit
def test_prayer_day_carries_boundary_markers() -> None:
    from dataclasses import replace

    day = _day()
    assert day.imsak == time(5, 35)
    assert day.dhuha == time(7, 25)
    assert isinstance(hash(day), int)
    with pytest.raises(FrozenInstanceError):
        day.imsak = time(5, 40)  # type: ignore[misc]
    assert day != replace(day, imsak=time(5, 36))


@pytest.mark.unit
def test_next_event_carries_boundary_pointer() -> None:
    from dataclasses import replace

    event = NextEvent(
        now=datetime(2025, 10, 20, 11, 45),
        state=PrayerState.NORMAL,
        next_prayer=MarkerName.DHUHR,
        adhan_at=datetime(2025, 10, 20, 12, 15),
        iqamah_at=datetime(2025, 10, 20, 12, 30),
        dim_until=datetime(2025, 10, 20, 12, 50),
        stale=False,
        next_boundary=MarkerName.IMSAK,
        boundary_at=datetime(2025, 10, 21, 5, 35),
    )
    assert event.next_boundary is MarkerName.IMSAK
    assert isinstance(hash(event), int)
    with pytest.raises(FrozenInstanceError):
        event.boundary_at = None  # type: ignore[misc]
    assert event != replace(event, boundary_at=datetime(2025, 10, 21, 5, 36))


@pytest.mark.unit
def test_settings_boundary_countdown_default_off() -> None:
    settings = Settings(masjid_name="M", zone="SGR01", hijri_offset=0)
    assert settings.boundary_countdown is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "marker", [MarkerName.IMSAK, MarkerName.SYURUQ, MarkerName.DHUHA]
)
def test_settings_rejects_boundary_marker_iqamah_rule(marker: MarkerName) -> None:
    with pytest.raises(ValueError, match="boundary time marker"):
        Settings(
            masjid_name="M",
            zone="SGR01",
            hijri_offset=0,
            iqamah_rules=(IqamahRule(prayer=marker, mode="delay"),),
        )


@pytest.mark.unit
def test_settings_rejects_fixed_rule_without_time() -> None:
    with pytest.raises(ValueError, match="fixed iqamah rule without time"):
        Settings(
            masjid_name="M",
            zone="SGR01",
            hijri_offset=0,
            iqamah_rules=(IqamahRule(prayer=MarkerName.FAJR, mode="fixed"),),
        )


@pytest.mark.unit
def test_settings_rejects_duplicate_prayer_rules() -> None:
    full = [
        IqamahRule(prayer=MarkerName.FAJR, mode="delay"),
        IqamahRule(prayer=MarkerName.DHUHR, mode="delay"),
        IqamahRule(prayer=MarkerName.ASR, mode="delay"),
        IqamahRule(prayer=MarkerName.MAGHRIB, mode="delay"),
        IqamahRule(prayer=MarkerName.ISHA, mode="delay"),
        IqamahRule(prayer=MarkerName.JUMUAH, mode="delay"),
    ]
    with pytest.raises(ValueError, match="duplicate iqamah rule for prayer: fajr"):
        Settings(
            masjid_name="M",
            zone="SGR01",
            hijri_offset=0,
            iqamah_rules=(*full, IqamahRule(prayer=MarkerName.FAJR, mode="delay")),
        )


@pytest.mark.unit
def test_settings_rejects_partial_prayer_rules() -> None:
    with pytest.raises(ValueError, match="missing iqamah rule for prayer"):
        Settings(
            masjid_name="M",
            zone="SGR01",
            hijri_offset=0,
            iqamah_rules=(IqamahRule(prayer=MarkerName.FAJR, mode="delay"),),
        )


@pytest.mark.unit
def test_settings_allows_empty_rules_for_unconfigured_iqamah() -> None:
    # Empty stays legal: resolve-time ConfigError owns it (domain/iqamah.py),
    # and the settings repo falls back to defaults for an empty rules table.
    settings = Settings(masjid_name="M", zone="SGR01", hijri_offset=0, iqamah_rules=())
    assert settings.iqamah_rules == ()


@pytest.mark.unit
def test_schedule_source_members() -> None:
    assert {s.value for s in ScheduleSource} == {"jakim", "calc", "manual"}


@pytest.mark.unit
def test_settings_boundary_offset_defaults_and_guards() -> None:
    settings = Settings(masjid_name="M", zone="SGR01", hijri_offset=0)
    assert settings.imsak_offset_min == 10
    assert settings.dhuha_offset_min == 28


@pytest.mark.unit
def test_settings_boundary_offset_ranges_rejected() -> None:
    for bad in (-1, 11):
        with pytest.raises(ValueError, match="imsak_offset_min out of range"):
            Settings(
                masjid_name="M",
                zone="SGR01",
                hijri_offset=0,
                imsak_offset_min=bad,
            )
    for bad in (14, 31):
        with pytest.raises(ValueError, match="dhuha_offset_min out of range"):
            Settings(
                masjid_name="M",
                zone="SGR01",
                hijri_offset=0,
                dhuha_offset_min=bad,
            )


@pytest.mark.unit
def test_settings_boundary_offset_edges_accepted() -> None:
    imsak_zero = Settings(
        masjid_name="M", zone="SGR01", hijri_offset=0, imsak_offset_min=0
    )
    assert imsak_zero.imsak_offset_min == 0
    dhuha_low = Settings(
        masjid_name="M", zone="SGR01", hijri_offset=0, dhuha_offset_min=15
    )
    assert dhuha_low.dhuha_offset_min == 15
    dhuha_high = Settings(
        masjid_name="M", zone="SGR01", hijri_offset=0, dhuha_offset_min=30
    )
    assert dhuha_high.dhuha_offset_min == 30


@pytest.mark.unit
def test_settings_countdown_defaults() -> None:
    settings = Settings(masjid_name="M", zone="SGR01", hijri_offset=0)
    assert settings.countdown_before_adhan_min == 5
    assert settings.countdown_before_adhan_overrides == {}


@pytest.mark.unit
def test_settings_countdown_default_range_rejected() -> None:
    for bad in (-1, 91):
        with pytest.raises(ValueError, match="countdown_before_adhan_min out of range"):
            Settings(
                masjid_name="M",
                zone="SGR01",
                hijri_offset=0,
                countdown_before_adhan_min=bad,
            )


@pytest.mark.unit
def test_settings_countdown_default_edges_accepted() -> None:
    assert (
        Settings(
            masjid_name="M",
            zone="SGR01",
            hijri_offset=0,
            countdown_before_adhan_min=0,
        ).countdown_before_adhan_min
        == 0
    )
    assert (
        Settings(
            masjid_name="M",
            zone="SGR01",
            hijri_offset=0,
            countdown_before_adhan_min=90,
        ).countdown_before_adhan_min
        == 90
    )


@pytest.mark.unit
def test_settings_countdown_overrides_round_trip() -> None:
    settings = Settings(
        masjid_name="M",
        zone="SGR01",
        hijri_offset=0,
        countdown_before_adhan_overrides={"fajr": 10},
    )
    assert settings.countdown_before_adhan_overrides == {"fajr": 10}
    assert settings.countdown_before_adhan_overrides["fajr"] == 10


@pytest.mark.unit
def test_settings_countdown_override_values_guarded() -> None:
    for bad in (-1, 91):
        with pytest.raises(ValueError, match="countdown override out of range"):
            Settings(
                masjid_name="M",
                zone="SGR01",
                hijri_offset=0,
                countdown_before_adhan_overrides={"fajr": bad},
            )


@pytest.mark.unit
def test_settings_countdown_override_rejects_unknown_and_boundary() -> None:
    with pytest.raises(ValueError, match="unknown prayer for countdown override"):
        Settings(
            masjid_name="M",
            zone="SGR01",
            hijri_offset=0,
            countdown_before_adhan_overrides={"bogus": 10},
        )
    with pytest.raises(ValueError, match="boundary time marker"):
        Settings(
            masjid_name="M",
            zone="SGR01",
            hijri_offset=0,
            countdown_before_adhan_overrides={"imsak": 10},
        )


@pytest.mark.unit
def test_countdown_window_override_wins_else_default() -> None:
    from muhideen.domain.countdown import countdown_window

    settings = Settings(
        masjid_name="M",
        zone="SGR01",
        hijri_offset=0,
        countdown_before_adhan_overrides={"fajr": 10},
    )
    assert countdown_window(settings, MarkerName.FAJR) == 10
    assert countdown_window(settings, MarkerName.DHUHR) == 5


@pytest.mark.unit
def test_theme_settings_defaults() -> None:
    from muhideen.core.values import ThemeSettings

    theme = ThemeSettings()
    assert theme.palette == "classic-green"
    assert theme.font == "outfit"
    assert theme.countdown_style == "boxes"
    assert theme.clock_format == "24h-seconds"
    assert theme.hijri_form == "long"
    assert theme.boundary_strip == "show"
    assert theme.density == "comfortable"


@pytest.mark.unit
def test_theme_settings_frozen_and_hashable() -> None:
    from dataclasses import FrozenInstanceError

    from muhideen.core.values import ThemeSettings

    theme = ThemeSettings()
    with pytest.raises(FrozenInstanceError):
        theme.palette = "midnight"  # type: ignore[misc]
    assert isinstance(hash(theme), int)
    assert theme == ThemeSettings()
    assert theme != ThemeSettings(palette="midnight")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("palette", "neon"),
        ("font", "comic-sans"),
        ("countdown_style", "spiral"),
        ("clock_format", "13h"),
        ("hijri_form", "roman"),
        ("boundary_strip", "marquee"),
        ("density", "airy"),
    ],
)
def test_theme_settings_rejects_open_values(field: str, bad: str) -> None:
    from muhideen.core.values import ThemeSettings

    with pytest.raises(ValueError, match=field):
        ThemeSettings(**{field: bad})  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "good"),
    [
        ("palette", "midnight"),
        ("palette", "sand"),
        ("font", "system"),
        ("countdown_style", "inline"),
        ("clock_format", "24h"),
        ("clock_format", "12h"),
        ("hijri_form", "short"),
        ("boundary_strip", "hide"),
        ("density", "compact"),
    ],
)
def test_theme_settings_accepts_closed_values(field: str, good: str) -> None:
    from muhideen.core.values import ThemeSettings

    assert getattr(ThemeSettings(**{field: good}), field) == good  # type: ignore[arg-type]


@pytest.mark.unit
def test_settings_carries_default_theme() -> None:
    from muhideen.core.values import ThemeSettings

    settings = Settings(masjid_name="M", zone="SGR01", hijri_offset=0)
    assert settings.theme == ThemeSettings()
    other = Settings(
        masjid_name="M",
        zone="SGR01",
        hijri_offset=0,
        theme=ThemeSettings(palette="midnight"),
    )
    assert settings != other
    assert isinstance(hash(other), int)


@pytest.mark.unit
def test_theme_seam_tables_are_single_source() -> None:
    from muhideen.core.values import (
        THEME_CHOICES,
        THEME_DEFAULTS,
        ThemeSettings,
        theme_from_kv,
        theme_pairs,
    )

    assert THEME_DEFAULTS == {
        "palette": "classic-green",
        "font": "outfit",
        "countdown_style": "boxes",
        "clock_format": "24h-seconds",
        "hijri_form": "long",
        "boundary_strip": "show",
        "density": "comfortable",
    }
    assert THEME_CHOICES["palette"] == ("classic-green", "midnight", "sand")
    assert THEME_CHOICES["font"] == ("outfit", "system")
    assert THEME_CHOICES["countdown_style"] == ("boxes", "inline")
    assert THEME_CHOICES["clock_format"] == ("24h", "24h-seconds", "12h")
    assert THEME_CHOICES["hijri_form"] == ("long", "short")
    assert THEME_CHOICES["boundary_strip"] == ("show", "hide")
    assert THEME_CHOICES["density"] == ("comfortable", "compact")
    assert theme_from_kv({}) == ThemeSettings()
    assert theme_pairs(ThemeSettings()) == [
        ("theme.palette", "classic-green"),
        ("theme.font", "outfit"),
        ("theme.countdown_style", "boxes"),
        ("theme.clock_format", "24h-seconds"),
        ("theme.hijri_form", "long"),
        ("theme.boundary_strip", "show"),
        ("theme.density", "comfortable"),
    ]
