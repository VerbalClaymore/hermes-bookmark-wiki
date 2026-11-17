#!/bin/zsh
cd -- "${0:A:h}/.." || exit 1
unset PYTHONPATH PYTHONHOME
export PYTHONDONTWRITEBYTECODE=1
exec /Users/vernon/.hermes/hermes-agent/venv/bin/python3 -B -m bookmark_wiki lane sync --vault "${BOOKMARK_VAULT:-/Users/vernon/Documents/BookmarkWiki}" "$@"
