# First-PC report review checklist

Stream: BE-GO-READY-INSTALL-017-FIRST-PC-REPORT-INTAKE-CHECKLIST

Operator template: `first-pc-validation-report-template.md`.

## 1. Intake -- did the report arrive in the expected shape?

- [ ] Commit hash is present.
- [ ] Working-tree status is present.
- [ ] Machine OS, CPU and RAM are present.
- [ ] Every executed command includes its complete output.
- [ ] A stop condition is stated when execution is incomplete.

## 2. Non-Docker path scoring (go-ready candidate)

- [ ] Installer preflight passed.
- [ ] Non-interactive install completed without a prompt.
- [ ] `/health` returned success.
- [ ] `/ready` returned success with all required checks OK.
- [ ] `cleanroom.ping` reached a terminal PostgreSQL task state.

Verdict: PASS / FAIL / INCOMPLETE

## 3. Docker path scoring (FIRST SCAFFOLD, NOT YET VALIDATED)

- [ ] `docker-validate.sh --dry-run` passed.
- [ ] Backend image build passed.
- [ ] Compose services became healthy.
- [ ] API readiness passed.
- [ ] Dramatiq cleanroom smoke passed.

Verdict: PASS / FAIL / INCOMPLETE

## 4. Stop-condition handling

- [ ] Read the reported stop point before scoring later steps.
- [ ] Mark every step after the stop point as NOT RUN.
- [ ] Preserve the first failing command and complete output.
- [ ] Create one actionable issue for each independent failure.

## 5. Outputs of the intake review

- [ ] Publish one intake summary line per installation path.
- [ ] Create a backend or installer task for each failing acceptance line.
- [ ] Create a documentation task for every ambiguous or stale instruction.
- [ ] Link all tasks back to the report revision and machine details.

## 6. What this checklist does NOT do

This checklist does not certify a machine by itself. It does not remove the
FIRST SCAFFOLD or NOT YET VALIDATED markers. Those markers can change only
after a complete real-machine report passes every acceptance line.
