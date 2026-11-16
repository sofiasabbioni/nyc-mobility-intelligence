from datetime import datetime
from decimal import Decimal

from equivalence import compare_results, values_equal


def test_equal_with_tolerance_and_types():
    assert compare_results([(1, 2.0000000001, "a")], [(1, 2.0, "a")])[0]
    assert compare_results([(Decimal("2.5"),)], [(2.5,)])[0]
    assert compare_results([(datetime(2026, 4, 1),)], [(datetime(2026, 4, 1),)])[0]
    assert compare_results([(None, float("nan"))], [(None, float("nan"))])[0]


def test_differences_are_found():
    assert not compare_results([(1,)], [(2,)])[0]
    assert not compare_results([(1.0,)], [(1.001,)])[0]
    assert not compare_results([(1,)], [(1,), (2,)])[0]
    assert not compare_results([(None,)], [(0,)])[0]
    assert not compare_results([(1, 2)], [(1,)])[0]
    ok, msg = compare_results([(1, "x")], [(1, "y")])
    assert not ok and "row 0, column 1" in msg


def test_order_matters():
    assert not compare_results([(1,), (2,)], [(2,), (1,)])[0]


def test_values_equal_bool_and_str():
    assert values_equal("a", "a") and not values_equal("a", "b") and not values_equal(True, False)
