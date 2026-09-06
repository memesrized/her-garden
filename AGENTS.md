## Core Development Rules

1. Code Quality
   - Type hints required for all code
   - Public APIs must have docstrings
   - Functions must be focused and small
   - Follow existing patterns exactly

2. Code Style
    - PEP 8 naming (snake_case for functions/variables)
    - Class names in PascalCase
    - Constants in UPPER_SNAKE_CASE
    - Document with google style docstrings
    - Use f-strings for formatting

## Development Philosophy

- **Simplicity**: Write simple, straightforward code
- **Readability**: Make code easy to understand
- **Performance**: Consider performance without sacrificing readability
- **Maintainability**: Write code that's easy to update
- **Testability**: Ensure code is testable
- **Reusability**: Create reusable components and functions
- **Less Code = Less Debt**: Minimize code footprint

## Coding Best Practices

- **Early Returns**: Use to avoid nested conditions
- **Descriptive Names**: Use clear variable/function names (prefix handlers with "handle")
- **DRY Code**: Don't repeat yourself
- **Minimal Changes**: Only modify code related to the task at hand
- **Function Ordering**: Define composing functions before their components
- **Simplicity**: Prioritize simplicity and readability over clever solutions
- **Build Iteratively** Start with minimal functionality and verify it works before adding complexity
- **Clean logic**: Keep core logic clean and push implementation details to the edges
- **File Organsiation**: Balance file organization with simplicity - use an appropriate number of files for the project scale
- **Reasonable class usage**: prefer classes in general over multple functions, but only if class is necessary to keep the logic together for easier maintenance

## Python Tooling Workflow

- This repo is `uv`-managed; use `uv` by default for Python environment and tooling tasks.
- For coding, testing, code review, or local development work, sync with `uv sync --group dev`.
- For runtime-only setup, `uv sync` is acceptable when dev tools are not needed.
- Prefer `uv run ...` for project entrypoints and Python tooling, including:
  - `uv run crossword ...`
  - `uv run crossword-web ...`
  - `uv run pytest`
  - `uv run mypy`
  - `uv run ruff check .`
  - `uv run ruff format .`
- Prefer the matching `Makefile` target when it expresses intent more clearly, especially `make check`, `make test`, `make lint`, `make typecheck`, and `make hooks`.
- When adding or updating dependencies, prefer `uv add <package>` and `uv add --dev <package>`.
- If `pyproject.toml` is edited manually for dependencies, run `uv lock` and keep `uv.lock` committed with the change.
- Do not default to `pip install`, `requirements.txt`, `python -m venv`, `source .venv/bin/activate`, or direct `.venv/bin/python` usage unless the user explicitly asks for that workflow or `uv` cannot satisfy the need.

## Documentation

- After each more or less major change of the code, update the state of the application in `docs/STATE.md` and `docs/state/` files
    - Start with `docs/STATE.md` for the high level state and then update the relevant detailed files in `docs/state/` for the specific areas that were changed
    - This is important to keep the state of the project up to date and to be able to track the progress and changes in the project over time, especially for LLMs that need to understand the current state of the project to be able to contribute effectively.
- When documenting code or state files, don't forget to:
    - Mention reasoning behind the decisions, not only what was done but why it was done, what alternatives were considered and why they were rejected, etc.
    - When documenting a decision with rejected alternatives, use this format for each alternative:
        - **Alternative**: [Name or short description]
        - **Description**: [What it would look like technically]
        - **Rejection reason**: [Why it was not selected]
    - Mention any known issues or limitations of the current implementation, so that they can be addressed in the future and so that LLMs can be aware of them when working with the code or state files.
    - Mention any future plans or next steps for the project, so that LLMs can understand the direction of the project and can contribute to it more effectively.

## Human based verification

### Pre-task spec (for non-trivial features)
- Before writing any code for a feature that takes more than ~30 min, write a 5-10 line plain-English spec:
    - What is being built
    - Key assumptions being made
    - What is explicitly NOT being done
- Wait for human confirmation before starting implementation
- This catches misunderstandings before any code exists

### Assumption surfacing
- When hitting an ambiguous design decision, surface it explicitly with a proposed default rather than choosing silently
- Example: "The config doesn't specify X. I'll do Y because Z - confirm?"

### Feature demo (notebook)
- After each new feature, add a small demo to a relevant existing notebook, or create `notebooks/demos/<feature>.ipynb`
- A demo is not a test - it exists solely so the human can read it and say "yes, this is what I wanted" or "no, that's wrong"
- A good demo must contain:
    1. A 1-2 sentence comment: what this shows, and what would have happened before this feature
    2. Realistic input from actual project data (`data/`), not toy examples
    3. Committed output (do not clear cell outputs before committing)
    4. Should run in under 30 seconds
- Include one non-trivial or edge case alongside the main happy-path case
- When a feature is removed or superseded, delete its demo

### "What I changed and why" summary
- After each feature, write a short (3-5 sentence) natural language note:
    - What changed
    - Why it was done this way
    - What was deliberately NOT changed, and why
- This goes at the top of the demo notebook cell or as a changelog entry
- Its purpose is to let the human spot "reasonable but not what I intended" decisions

## Diagrams and module orientation

### Mermaid diagrams in state docs
- Key state docs (`docs/state/system/data_flow.md`, architecture docs) should include a Mermaid diagram where it helps orientation
- The data flow diagram must show: pipeline stages, key data structures passed between stages, and where config enters
- Update the diagram when the pipeline structure changes - a stale diagram is worse than no diagram
- Goal: the human should be able to orient in 10 seconds without reading multiple files

### Module-level docstrings
- Every module in `ddd/` must have a docstring at the top of its `__init__.py` (or main file if no `__init__.py`) covering:
    - What the module does (1-2 sentences)
    - What it receives as input
    - What it produces as output
    - One key design decision or constraint worth knowing
- Keep it to 5-10 lines - this is a quick orientation aid, not full documentation
- Update it when the module's role or interface changes
