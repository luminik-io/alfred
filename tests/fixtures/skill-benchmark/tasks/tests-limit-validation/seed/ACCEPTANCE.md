# Limit validation

- `normalize_limit(None)` returns 10.
- Integers from 1 through 100 return unchanged.
- Zero, negative integers, integers above 100, strings, and booleans raise `ValueError`.
- Add tests only. Keep the production function unchanged.
