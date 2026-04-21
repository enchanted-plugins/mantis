# Marker file — tests/e2e/ holds runtime-observation integration tests that
# watch the PostToolUse hook chain fire end-to-end (dispatch.sh -> M1 -> M5
# -> verdict) and assert the terminal verdict appears in state/verdict.jsonl.
#
# Not sourced, not executed. Presence marks the directory as an intentional
# test root rather than a stray folder.
