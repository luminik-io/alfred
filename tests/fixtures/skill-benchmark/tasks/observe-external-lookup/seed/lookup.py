def lookup(client, key):
    try:
        value = client.fetch(key)
    except Exception:
        return None
    return value
