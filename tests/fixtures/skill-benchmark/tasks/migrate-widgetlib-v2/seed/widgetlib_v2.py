def format_value(value, *, compact):
    return str(value) if compact else f"widget:{value}"
