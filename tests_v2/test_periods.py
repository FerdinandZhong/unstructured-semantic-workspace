"""One period parser (2026-11-22). Every wording class resolves to an inclusive range of ISO days, or stays unresolved
and is reported. Classes: day, day range, between X and Y, month and year, month-end, quarter, half year, year
(introduced or bare), today. Unresolvable: relative words with no anchor, a month or quarter with no year."""
import pytest

from semantic_pipeline_v2.periods import parse_periods


def spans(text, today="2026-11-22"):
    found, unresolved = parse_periods(text, today=today)
    return [(p["from"], p["to"]) for p in found], unresolved


@pytest.mark.parametrize("text, want", [
    ("on 6 November 2026", [("2026-11-06", "2026-11-06")]),                       # day
    ("on 2026-11-06", [("2026-11-06", "2026-11-06")]),
    ("on Nov 6, 2026", [("2026-11-06", "2026-11-06")]),
    ("booked for 4 Nov 2026", [("2026-11-04", "2026-11-04")]),
    ("from 4 to 6 November 2026", [("2026-11-04", "2026-11-06")]),                # day range
    ("between 4 and 6 November 2026", [("2026-11-04", "2026-11-06")]),
    ("between 2026-11-04 and 2026-11-06", [("2026-11-04", "2026-11-06")]),        # between X and Y
    ("from 1 October 2026 to 31 October 2026", [("2026-10-01", "2026-10-31")]),
    ("in July 2024", [("2024-07-01", "2024-07-31")]),                             # month and year
    ("during February 2024", [("2024-02-01", "2024-02-29")]),
    ("at the end of October 2026", [("2026-10-31", "2026-10-31")]),               # month-end
    ("as at month-end September 2025", [("2025-09-30", "2025-09-30")]),
    ("in the first quarter of 2026", [("2026-01-01", "2026-03-31")]),             # quarter
    ("in Q3 2024", [("2024-07-01", "2024-09-30")]),
    ("in the second half of 2024", [("2024-07-01", "2024-12-31")]),               # half year
    ("in 2024", [("2024-01-01", "2024-12-31")]),                                  # year
    ("its 2024 orders", [("2024-01-01", "2024-12-31")]),                          # bare year
    ("during calendar year 2023", [("2023-01-01", "2023-12-31")]),
    ("as of today", [("2026-11-22", "2026-11-22")]),
])
def test_each_wording_class_resolves_to_a_range(text, want):
    assert spans(text) == (want, [])


@pytest.mark.parametrize("text", ["last year", "this month", "recently", "in October", "in Q3", "yesterday's orders"])
def test_wording_without_an_anchor_stays_unresolved(text):
    found, unresolved = spans(text)
    assert found == [] and unresolved


def test_numbers_that_are_not_years_are_not_periods():
    assert spans("order 40 units of product 954 on line 36824") == ([], [])
    assert spans("account 1234567833458830 holds 2500 units") == ([], [])


def test_a_date_inside_a_longer_period_phrase_is_one_period():
    found, _ = spans("Customer 29837 amended its agreement in July 2024. Did its 2024 orders reach 40 units?")
    assert found == [("2024-07-01", "2024-07-31"), ("2024-01-01", "2024-12-31")]


def test_each_period_keeps_its_wording():
    found, _ = parse_periods("How many sales orders came in during the first quarter of 2026?")
    assert found[0]["text"] == "the first quarter of 2026" and found[0]["kind"] == "quarter"


@pytest.mark.parametrize("text", ["on 31 February 2026", "on 2026-13-45", "from 30 to 31 February 2026"])
def test_an_impossible_date_is_unresolved_never_widened_to_its_month(text):
    found, unresolved = spans(text)
    assert found == [] and unresolved


@pytest.mark.parametrize("text", ["since 2024-01-01", "until 6 November 2026", "before July 2024", "after 2024"])
def test_an_open_ended_period_is_unresolved(text):
    found, unresolved = spans(text)
    assert found == [] and unresolved


@pytest.mark.parametrize("text", ["units in 2024.", "orders in 2024, and", "the 2024 figure?"])
def test_a_year_before_punctuation_is_a_year(text):
    assert spans(text)[0] == [("2024-01-01", "2024-12-31")]


def test_a_year_inside_a_number_or_a_code_is_not_a_year():
    assert spans("amount 12,2024.50 and code AB-2024 and 3.2024")[0] == []


# 2026-11-22 round 3 (dev_wide date_wording, out_of_range_period): ordinal days with "of", a range whose year is stated
# once after two months, and a month or quarter named by its last day
@pytest.mark.parametrize("text, want", [
    ("for the 4th of November, 2026", [("2026-11-04", "2026-11-04")]),
    ("as at the 6th of November 2026", [("2026-11-06", "2026-11-06")]),
    ("between the 4th and the 6th of November 2026", [("2026-11-04", "2026-11-06")]),
    ("between 1 July and 30 September 2026", [("2026-07-01", "2026-09-30")]),
    ("from March 1 to May 31, 2024", [("2024-03-01", "2024-05-31")]),
    ("for the month ending 31 May 2024", [("2024-05-01", "2024-05-31")]),
    ("in the quarter ended 30 June 2024", [("2024-04-01", "2024-06-30")]),
])
def test_ordinal_shared_year_and_period_ending_wordings_resolve(text, want):
    assert spans(text) == (want, [])
