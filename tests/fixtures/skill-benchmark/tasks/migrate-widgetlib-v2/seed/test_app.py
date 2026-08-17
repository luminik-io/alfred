import unittest

from app import describe_widget


class AppTests(unittest.TestCase):
    def test_full_widget_description(self):
        self.assertEqual(describe_widget("blue"), "widget:blue")
