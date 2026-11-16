# Bushido T1981 console

A Mac drives a Tacx Bushido T1981 brake over ANT, replacing its
dead T1982 head unit. Read `README.md` first: layout, verified protocol, how the page works.

## Commands

    .venv/bin/python -m pytest -q                              # must pass before any commit
    .venv/bin/python -m bushido.app                            # real brake, http://127.0.0.1:8981
    .venv/bin/python -m bushido.app --simulate --port 8982     # fake brake, data in sim-data/
    node --check bushido/static/app.js
    node tools/page-check.mjs [port]                           # walks every screen on the simulator, no browser
    node tools/screenshots.mjs [port]                          # every screen -> sim-data/shots/ (headless Chrome)

## Rules

- Python: type hints, constants in `bushido/constants.py`, pure logic gets a test in `tests/`.
- The page is plain HTML/CSS/JS in `bushido/static/` (no build). Every clickable element
  carries `data-action`, handled in the `actions` map of `app.js`. UI text is French, in
  plain words for a rider, not for a developer.
- Never test against `sessions/` or `profiles.json` (real rides): use `--simulate`.
- Only one process can hold the ANT stick: do not start the real app if another instance is running.
- The brake is self-powered: nothing can be tested on it unless someone pedals.
- Protocol facts must come from a capture on the real brake (`captures/`), not from the
  public spec alone: it was wrong about the temperature byte and the calibration commands.
