# tool-selection-bench

When an LLM agent has several web tools available, which one does it pick for a task, and was that the right pick?

Work in progress. Runs entirely on free tiers (Gemini, Firecrawl, TinyFish). See NOTES.md for design decisions.

```
uv sync
cp .env.example .env   # add keys; tools with missing keys are skipped
uv run pytest -q
uv run python -m bench smoke
```

If a run stops on a free quota (exit code 3), rerun the same command later to resume.
