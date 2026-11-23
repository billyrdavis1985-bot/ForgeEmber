# Prompts

One file per prompt per version (`<role>.v1.md`, `<role>.v2.md`). A prompt is never edited
in place once used in a recorded run: add a new version instead. The client records each
prompt's `prompt_version` and `prompt_sha256` in the trace.
