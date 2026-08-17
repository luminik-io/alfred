def publish(queue, topic, payload):
    if not topic:
        return False
    try:
        queue.send(topic, payload)
    except Exception:
        return False
    return True
