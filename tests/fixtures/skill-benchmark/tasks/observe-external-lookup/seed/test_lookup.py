import unittest

from lookup import lookup


class Client:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    def fetch(self, key):
        if self.error:
            raise self.error
        return self.value


class LookupContractTests(unittest.TestCase):
    def test_returns_client_value(self):
        self.assertEqual(lookup(Client("value"), "secret-key"), "value")

    def test_returns_none_on_client_error(self):
        self.assertIsNone(lookup(Client(error=RuntimeError("down")), "secret-key"))
