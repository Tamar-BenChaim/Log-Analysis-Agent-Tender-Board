"""
PyInstaller entry point (Story SCRUM-188).

PyInstaller needs a top-level script it can treat as `__main__`; the
real CLI logic lives in agent/cli.py (which is imported as part of the
`agent` package everywhere else - `python -m agent.cli ...`). Running
PyInstaller directly on agent/cli.py would make PyInstaller's analysis
root agent/'s own directory, where `from agent.graph import ...`
cannot resolve (there'd be no `agent` package visible from inside
`agent/` itself). This tiny script, run from the project root, avoids
that: `agent` resolves normally since the project root is on sys.path.
"""

from agent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
