"""Weak on purpose: looks complete, misses every boundary."""
from grades import grade_point, has_passed, letter_grade, percentage


def test_top_grade():
    assert letter_grade(95) == "S"


def test_fail_grade():
    assert letter_grade(30) == "U"


def test_grade_point_middle():
    assert grade_point(75) == 8


def test_clear_pass():
    assert has_passed(30, 50) is True


def test_percentage_half():
    assert percentage(50, 100) == 50.0
