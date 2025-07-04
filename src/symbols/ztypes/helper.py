
from datetime import datetime

BASE_DT = datetime(1970, 1, 1)
# Approximation for seconds in different durations
SECONDS_IN_YEAR = 365 * 24 * 3600
SECONDS_IN_MONTH = 30 * 24 * 3600
SECONDS_IN_DAY = 24 * 3600
SECONDS_IN_HOUR = 3600
SECONDS_IN_MINUTE = 60


def extract_part_from_date_symbol(symbol, format_str):
    """
        Extract a specific part of a Z3DateTime object using a format string.
        Supported format strings:
        - %Y: Year
        - %m: Month
        - %d: Day
        - %H: Hour
        - %M: Minute
        - %S: Second
        """
    if format_str == "%Y":
        return symbol / SECONDS_IN_YEAR
    elif format_str == "%m":
        years_elapsed = symbol  / SECONDS_IN_YEAR
        remaining_seconds = symbol  - (years_elapsed * SECONDS_IN_YEAR)
        return 1 + remaining_seconds / SECONDS_IN_MONTH  # Months start at 1
    elif format_str == "%d":
        days_elapsed = symbol  / SECONDS_IN_DAY
        return 1 + days_elapsed % 30  # Approximation: Assume 30-day months
    elif format_str == "%H":
        return (symbol  % SECONDS_IN_DAY) / SECONDS_IN_HOUR
    elif format_str == "%M":
        return (symbol  % SECONDS_IN_HOUR) / SECONDS_IN_MINUTE
    elif format_str == "%S":
        return symbol  % SECONDS_IN_MINUTE
    else:
        raise ValueError(f"Unsupported format string: {format_str}")