def normalize_limit(value):
    if value is None:
        return 10
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("limit must be an integer")
    if value < 1 or value > 100:
        raise ValueError("limit must be between 1 and 100")
    return value
