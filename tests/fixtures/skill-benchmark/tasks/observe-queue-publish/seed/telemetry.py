from contextlib import contextmanager

events = []
metrics = []
spans = []
enabled = True


def log_event(name, **fields):
    if enabled:
        events.append((name, fields))


def increment(name, **labels):
    if enabled:
        metrics.append((name, labels))


@contextmanager
def span(name, **attributes):
    record = {"name": name, "attributes": attributes, "status": "ok"}
    spans.append(record)
    try:
        yield record
    except Exception as exc:
        record["status"] = "error"
        record["error_class"] = type(exc).__name__
        raise


def reset():
    events.clear()
    metrics.clear()
    spans.clear()
