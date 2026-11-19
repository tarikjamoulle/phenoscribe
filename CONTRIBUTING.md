# Contributing

## Set up

```bash
uv sync
uv run phenoscribe setup --no-models   # the HPO file and the search index for the tests
```

## Test and lint

```bash
uv run python -m pytest -m "not network and not live"
uv run ruff check
```

- `network` tests download a model or the HPO release.
- `live` tests call a paid LLM API. They skip without `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`.
- `needs_hpo_data` tests skip until `phenoscribe setup --no-models` has run.

CI runs the same command on Linux and macOS.

## Prompts and privacy rules

A change to an LLM prompt or to a privacy rule changes the codes or what reaches the LLM.

- A prompt change: run the benchmark scripts again (`scripts/README.md`) and update the
  sha256 freeze in `tests/test_english.py` in the same change.
- A privacy change (a model, a regex, the leak check, the review rule): run
  `scripts/eval_pii.py` again and update the numbers in `README.md`.

## Licence of your contribution

Phenoscribe is under AGPL-3.0-or-later. Before a pull request is merged, you must agree to
`CLA.md`. You keep the copyright of your work. The agreement lets the maintainer publish
your work under AGPL-3.0 and under other licences, a commercial licence included.

To agree, write this sentence in your pull request:

> I have read CLA.md and I agree to it for all my contributions to Phenoscribe.

## Report a privacy or security problem

Do not open a public issue. Use GitHub private vulnerability reporting on this repository
(the **Security** tab, **Report a vulnerability**). See `SECURITY.md`.
