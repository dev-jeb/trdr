import pytest

from ..security_provider.models import Timeframe
from ...test_utils.security_generator import SecurityCriteria, Crossover


def test_compute_average_volume(get_random_security):
    security = get_random_security
    d5_average_volume = sum(bar.volume for bar in security.bars[-5:]) // 5
    d20_average_volume = sum(bar.volume for bar in security.bars[-20:]) // 20
    assert security.compute_average_volume(Timeframe.d5) == d5_average_volume
    assert security.compute_average_volume(Timeframe.d20) == d20_average_volume


def test_compute_moving_average(get_random_security):
    security = get_random_security
    d5_moving_average = sum(bar.close.amount for bar in security.bars[-5:]) / 5
    assert security.compute_moving_average(Timeframe.d5).amount == d5_moving_average


def test_get_current_price_and_volume(get_random_security):
    security = get_random_security
    assert security.get_current_price() == security.current_bar.close
    assert security.get_current_volume() == security.current_bar.volume


def test_invalid_timeframe(get_random_security):
    security = get_random_security
    with pytest.raises(ValueError):
        security.compute_average_volume(None)
    with pytest.raises(ValueError):
        security.compute_moving_average(None)


def test_bullish_crossover(security_generator):
    crossover = Crossover(type="golden_cross", ma1=Timeframe.d5, ma2=Timeframe.d20)
    criteria = SecurityCriteria(bar_count=200, crossovers=[crossover])
    generator = security_generator
    generator.criteria = criteria
    security = generator.find_suitable_security()
    result = security.has_bullish_moving_average_crossover(Timeframe.d5, Timeframe.d20)
    assert result is True


def test_bearish_crossover(security_generator):
    crossover = Crossover(type="death_cross", ma1=Timeframe.d5, ma2=Timeframe.d20)
    criteria = SecurityCriteria(bar_count=200, crossovers=[crossover])
    generator = security_generator
    generator.criteria = criteria
    security = generator.find_suitable_security()
    result = security.has_bearish_moving_average_crossover(Timeframe.d5, Timeframe.d20)
    assert result is True


def test_compute_average_volume_with_offset(get_random_security):
    security = get_random_security

    large_offset = len(security.bars) - 2
    assert security.compute_average_volume(Timeframe.d5, offset=large_offset) is None
    assert security.compute_average_volume(Timeframe.d5, offset=len(security.bars)) is None


def test_compute_moving_average_with_offset(get_random_security):
    security = get_random_security
    large_offset = len(security.bars) - 2
    assert security.compute_moving_average(Timeframe.d5, offset=large_offset) is None
    assert security.compute_moving_average(Timeframe.d5, offset=len(security.bars)) is None


def test_compute_average_volume_zero_days(get_random_security):
    security = get_random_security

    with pytest.raises(ValueError):
        security.compute_average_volume(Timeframe.m15)


def test_compute_rsi(get_random_security):
    security = get_random_security
    rsi = security.compute_rsi(14)
    assert rsi is not None
    assert 0 <= rsi <= 100


def test_compute_rsi_insufficient_data(get_random_security):
    security = get_random_security
    result = security.compute_rsi(14, offset=len(security.bars))
    assert result is None


def test_compute_ema(get_random_security):
    security = get_random_security
    ema = security.compute_ema(20)
    assert ema is not None
    assert ema.amount > 0


def test_compute_ema_vs_sma(get_random_security):
    """EMA and SMA over same period should be in the same ballpark."""
    security = get_random_security
    ema = security.compute_ema(20)
    sma = security.compute_moving_average(Timeframe.d20)
    assert ema is not None and sma is not None
    # They won't be equal but should be within 10% of each other
    ratio = ema.amount / sma.amount
    assert 0.9 < ratio < 1.1


def test_compute_macd(get_random_security):
    security = get_random_security
    result = security.compute_macd()
    assert result is not None
    macd_line, signal, histogram = result
    # Histogram should equal line minus signal
    assert abs(histogram - (macd_line - signal)) < 0.0001


def test_compute_macd_insufficient_data(get_random_security):
    from ...test_utils.security_generator import SecurityGenerator, SecurityCriteria
    generator = SecurityGenerator(SecurityCriteria(bar_count=30))
    security = generator.find_suitable_security()
    result = security.compute_macd()
    assert result is None


def test_compute_atr(get_random_security):
    security = get_random_security
    atr = security.compute_atr(14)
    assert atr is not None
    assert atr > 0


def test_compute_atr_insufficient_data(get_random_security):
    security = get_random_security
    result = security.compute_atr(14, offset=len(security.bars))
    assert result is None


def test_compute_bollinger_bands(get_random_security):
    security = get_random_security
    upper = security.compute_bollinger_band(upper=True)
    lower = security.compute_bollinger_band(upper=False)
    sma = security.compute_moving_average(Timeframe.d20)
    assert upper is not None and lower is not None and sma is not None
    assert upper.amount > sma.amount
    assert lower.amount < sma.amount
    assert upper.amount > lower.amount


def test_daily_high_low(get_random_security):
    security = get_random_security
    assert security.current_bar.high.amount >= security.current_bar.low.amount


def test_compute_percent_change(get_random_security):
    security = get_random_security
    pct = security.compute_percent_change()
    assert pct is not None
    # Manually verify
    today = security.bars[-1].close.amount
    yesterday = security.bars[-2].close.amount
    expected = (today - yesterday) / yesterday * 100
    assert abs(pct - expected) < 0.0001


def test_compute_percent_change_insufficient_data(get_random_security):
    security = get_random_security
    result = security.compute_percent_change(offset=len(security.bars))
    assert result is None
