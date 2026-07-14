from pydantic import BaseModel
import numpy as np


class Example(BaseModel):
    """
    Example class


    Attributes
    ----------
    factor : float
        Power factor for calculation
    """

    factor: float

    def calculation(self, a: np.array, b: np.array) -> np.array:
        """
        Perform a probabilistic calculation.

        Parameters
        ----------
        a: np.array
            a parameter.
        b: np.array
            b parameter

        Returns
        -------
        Results: Updated results.
        """

        # Perform the calculation
        result = (a + b) ** self.factor

        return result
