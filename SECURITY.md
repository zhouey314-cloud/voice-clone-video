# Security and privacy

## Credentials

Never commit `config.local.env`, API keys, tokens, private authorization records,
identity documents, raw reference recordings, or generated media. Use environment
variables or the macOS Keychain for local credentials. The public repository only
contains `config.local.env.example`, whose key value is intentionally empty.

If a credential was accidentally committed, revoke or rotate it immediately and
remove it from the repository history before making the repository public.

## Voice consent

Only use a reference recording when the voice owner or an authorized representative
has approved the exact synthetic-narration use. Keep the signed or traceable consent
record outside this repository. Do not use this project for impersonation, fraud,
identity verification, OTP/account recovery, political persuasion, or financial or
medical deception.

## Reporting an issue

For a suspected security issue, do not open a public issue with credentials or
private recordings. Contact the repository owner privately with reproduction details
that do not include secret material.
