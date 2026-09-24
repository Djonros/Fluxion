---
name: Feature request
about: Suggest an idea for Fluxion
title: "[feat]: "
labels: ["enhancement"]
body:
  - type: textarea
    id: problem
    attributes:
      label: Problem
      description: What problem does this feature solve? Link a related Discussion if one exists.
    validations:
      required: true
  - type: textarea
    id: solution
    attributes:
      label: Proposed solution
      description: Describe the behaviour you expect, optional alternatives.
    validations:
      required: true
