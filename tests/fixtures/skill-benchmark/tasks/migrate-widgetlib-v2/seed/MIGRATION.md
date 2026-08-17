# widgetlib 2.0.0

- Replace `widgetlib_v1.render(value)` with `widgetlib_v2.format_value(value, compact=False)`.
- `format_value` returns the same full string when `compact=False`.
- Pin both the project manifest and lockfile to 2.0.0.
- Remove every application import of `widgetlib_v1`.
