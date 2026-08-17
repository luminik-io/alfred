import unittest

from publish import publish


class Queue:
    def __init__(self, error=None):
        self.error = error

    def send(self, topic, payload):
        if self.error:
            raise self.error


class PublishContractTests(unittest.TestCase):
    def test_returns_true_after_send(self):
        self.assertTrue(publish(Queue(), "events", {"token": "private"}))

    def test_returns_false_for_blank_topic(self):
        self.assertFalse(publish(Queue(), "", {"token": "private"}))

    def test_returns_false_on_queue_error(self):
        self.assertFalse(publish(Queue(error=RuntimeError("down")), "events", {"token": "private"}))
