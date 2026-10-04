# Contributing to Fluxion

Thank you for your interest in improving Fluxion! This document covers the development workflow and coding standards.

## Quick Start

```bash
git clone https://github.com/djonros/fluxion.git
cd fluxion
python -m venv .venv
.venv\Scripts\activate     # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
python -m pytest tests/ -v
```

## Development Workflow

1. **Find a task** — check issues labeled [`good first issue`](https://github.com/djonros/fluxion/labels/good%20first%20issue) for starter-friendly tasks, or propose your own in [Discussions](https://github.com/djonros/fluxion/discussions).

2. **Fork & branch** — create a feature branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```

3. **Write tests first** — every new feature or bug fix should include tests; a bug fix should come with a test that fails without the fix. The whole suite must pass.

4. **Run tests locally** before submitting:
   ```bash
   python -m pytest tests/ -v --tb=short
   ```

5. **Keep commits focused** — one logical change per commit, clear message:
   ```
   feat(agent): add write_file tool with path validation
   fix(rag): handle empty chunks in retriever
   docs: update README installation steps
   ```

6. **Submit a Pull Request** — describe what changed, why, and how it was tested.

## Coding Standards

- **Python 3.11+** — use modern syntax (`match/case`, `type | None`, f-strings).
- **`from __future__ import annotations`** at the top of every module.
- **Type hints** on all public functions and methods.
- **Docstrings** — triple-quote, describe parameters and return value.
- **No comments** unless the code is genuinely non-obvious. Prefer clear names.
- **4-space indentation**, max line length ~100 characters.

### Commit Message Convention

| Prefix | Scope | Example |
|--------|-------|---------|
| `feat` | New feature | `feat(agent): add git_diff tool` |
| `fix` | Bug fix | `fix(retriever): handle empty query` |
| `refactor` | Code restructuring | `refactor(inference): extract _options method` |
| `test` | Test additions | `test(agent): add edit_file edge cases` |
| `docs` | Documentation | `docs: add Phase 8 to docs/internal/ROADMAP.md` |
| `chore` | Maintenance | `chore: update dependencies` |

## Project Structure

```
core/           Model backend abstraction, config, Ollama client
rag/            Chunker, indexer, retriever, reranking
web/            SearXNG client, content fetcher, web search pipeline
orchestrator/   Router, prompt builder, assistant, ReAct agent
cli/            REPL, command registry, renderer
eval/           HumanEval evaluation framework
data/           Datasets, ChromaDB, repos (gitignored)
config/         YAML configuration
tests/          Pytest suite; on Windows run it by groups with fluxion-test.bat
```

## Reporting Issues

- **Questions & ideas** — start a thread in [GitHub Discussions](https://github.com/djonros/fluxion/discussions) (Q&A + feature requests).
- **Bugs** — open an issue using the *Bug report* template; include Python version, OS, error traceback, and minimal reproduction steps.
- **Feature requests** — open an issue using the *Feature request* template, or discuss it first in Discussions.
- **Security issues** — email <djonros@gmail.com> directly, do not open a public issue.

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
