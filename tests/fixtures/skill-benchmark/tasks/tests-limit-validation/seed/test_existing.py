import unittest

from limits import normalize_limit


class ExistingLimitTests(unittest.TestCase):
    def test_typical_limit(self):
        self.assertEqual(normalize_limit(25), 25)
