## What this changes

<!-- One or two sentences. Link the issue if there is one. -->

## How it was tested

<!-- Paste the command and its output. "Should work" is not a test. -->

```
```

## Checklist

- [ ] `python -m pytest` passes locally, with rclone installed so the
      end-to-end tests actually run
- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] No new runtime dependency, or the reason is explained above
- [ ] No inline styles in the web UI (the CSP blocks them silently)
- [ ] If a security guard changed, its test in `tests/test_server_security.py`
      changed with it
- [ ] Screenshots below for anything visual, in light and dark appearance
