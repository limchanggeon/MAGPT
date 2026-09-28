#!/bin/zsh
cd "${0:A:h}"
if [[ -x .venv/bin/python ]]; then
  .venv/bin/python -m mepiti
else
  python3 -m mepiti
fi
