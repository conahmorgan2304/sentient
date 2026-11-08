"""Sensor numbers must be finite before thresholds, statistics or rounding."""
import math


def finite_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None
