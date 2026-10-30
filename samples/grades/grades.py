"""Sample module: exam grading (example scale, not any university's official one)."""

GRADE_POINTS = {"S": 10, "A+": 9, "A": 8, "B+": 7, "B": 6, "U": 0}


def letter_grade(marks: int) -> str:
    """90+ S, 80+ A+, 70+ A, 60+ B+, 50+ B, below 50 U. Marks must be 0-100."""
    if marks < 0 or marks > 100:
        raise ValueError("marks must be between 0 and 100")
    if marks >= 90:
        return "S"
    if marks >= 80:
        return "A+"
    if marks >= 70:
        return "A"
    if marks >= 60:
        return "B+"
    if marks >= 50:
        return "B"
    return "U"


def grade_point(marks: int) -> int:
    return GRADE_POINTS[letter_grade(marks)]


def has_passed(internal: int, external: int) -> bool:
    """Internal is out of 40, external out of 60.
    Pass needs at least 27 in external AND a total of at least 50."""
    return external >= 27 and internal + external >= 50


def percentage(obtained: int, total: int) -> float:
    if total <= 0:
        raise ValueError("total must be positive")
    return round(obtained * 100 / total, 2)
