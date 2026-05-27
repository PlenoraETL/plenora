# First PC Report Review Checklist

Stream: BE-GO-READY-INSTALL-017-FIRST-PC-REPORT-INTAKE-CHECKLIST

Companion docs:
- first-pc-validation-report-template.md

## 1. Intake -- did the report arrive in the expected shape?

- Confirm commit and working tree output are present.
- Confirm every requested command has output.
- Confirm any stopped run includes exit code and log tail.

## 2. Non-Docker path scoring (go-ready candidate)

- Score preflight, install, health, ready, and cleanroom.ping.
- Record each missing or failing command as a separate backend task.

## 3. Docker path scoring (FIRST SCAFFOLD, NOT YET VALIDATED)

- Keep Docker marked FIRST SCAFFOLD and NOT YET VALIDATED until a real report is green.
- Score dry-run and backend build separately.

## 4. Stop-condition handling

- If the report stopped early, review the stop reason before scoring later sections.
- Do not infer success for commands that were not run.

## 5. Outputs of the intake review

- Produce one intake summary line.
- Produce one backend task for each failing acceptance line.
- Produce doc-fix tasks for confusing or missing instructions.

## 6. What this checklist does NOT do

- This checklist is a review process, not a validation result.
- Only a reviewed filled report can mark a path as accepted.
