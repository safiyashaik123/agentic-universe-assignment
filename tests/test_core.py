from datetime import date
from solution.main import norm_phone, parse_date, parse_decimal, parse_ts, IST
import pytest

def test_phone_formats():
    assert norm_phone('9876543210') == '+919876543210'
    assert norm_phone('+91 98765 43210') == '+919876543210'
    assert norm_phone('09123456789') == '+919123456789'
    with pytest.raises(ValueError): norm_phone('1234567890')

def test_dates_day_first_and_invalid():
    assert parse_date('01-Dec-2026') == '2026-12-01'
    assert parse_date('03/10/2026') == '2026-10-03'
    with pytest.raises(ValueError): parse_date('31/02/2026')

def test_decimal_currency():
    assert parse_decimal('₹12,345.678') == '12345.68'
    assert parse_decimal('Rs. 1,200') == '1200.00'
    with pytest.raises(ValueError): parse_decimal('-2')

def test_naive_timestamp_is_ist():
    assert parse_ts('2026-10-03 10:01:00').utcoffset() == IST.utcoffset(None)
    assert parse_ts('2026-10-03T04:31:00Z').date().isoformat() == '2026-10-03'
