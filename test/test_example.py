import numpy as np

from python_package_template import Example


def test_Example():
    example = Example(factor=2)
    result = example.calculation(np.array([1, 2, 3]), np.array([4, 5, 6]))
    assert np.array_equal(result, np.array([25, 49, 81]))
