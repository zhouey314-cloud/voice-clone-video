# Contributing

Thanks for helping improve Voice Clone Video.

## Before opening a change

- Do not include API keys, private consent records, raw recordings, generated media,
  or provider responses containing sensitive data.
- Preserve the approved-consent gate and the human review boundary.
- Keep mock fixtures clearly labelled `DEMO_ONLY`.
- Prefer small, dependency-light changes that work on Python 3.10+.

## Local checks

```bash
python -m unittest discover -s tests -p 'test_*.py'
python scripts/validate_run.py --skill-dir .
```

Changes that affect real voice behavior should include a regression case or a clear
explanation of why a reliable offline case is not possible.
