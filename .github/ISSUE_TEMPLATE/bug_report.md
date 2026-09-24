---
name: Bug report
about: Report something that is broken
title: "[bug]: "
labels: ["bug"]
body:
  - type: textarea
    id: description
    attributes:
      label: Description
      description: A clear description of what is broken and what you expected instead.
    validations:
      required: true
  - type: textarea
    id: reproduction
    attributes:
      label: Steps to reproduce
      placeholder: |
        1. Run `python -m cli.app`
        2. Execute `/index .`
        3. See error
    validations:
      required: true
  - type: textarea
    id: logs
    attributes:
      label: Error output / logs
      render: shell
  - type: input
    id: environment
    attributes:
      label: Environment
      description: OS, Python version, backend (Ollama / API / llama.cpp), model.
      placeholder: "Windows 11, Python 3.12, Ollama, qwen2.5-coder:7b-instruct"
    validations:
      required: true
