# Local-first reference

The goal is a fast feedback loop for developers and agents, not a weaker remote gate.

This document is language-, framework-, and project-neutral. Use the repository's own commands and runtime; do not assume a package manager, test runner, database, or directory layout.

## Split matrix

| Work | Local | Remote | Notes |
| --- | --- | --- | --- |
| Lint, format, type-check | Yes | Thin final check | Same versions as CI |
| Unit tests | Yes | Final verification | Full suite before merge if it is the required check |
| Integration tests | If dependencies are reproducible locally | Yes | Needs service/environment parity |
| End-to-end tests | Optional smoke | Yes | Browser/infrastructure dependencies usually need CI |
| Security and compliance scans | No | Yes | Required by policy |
| Build | Reproducible locally | Yes | Publish artifacts remotely |
| Deploy | No | Yes | Protected environments and approvals |

## Implementation pattern

Create one documented command:

```bash
make ci-local
# or
bin/ci
```

It should:

1. Use the same container image, runtime, and tool versions as CI.
2. Run lint, type-check, and the fast test suite in the same order as CI.
3. Fail fast and print a clear reason.
4. Be usable by both developers and agents without interactive prompts.
5. Accept a scope argument for targeted runs, for example `bin/ci --only <suite-or-scope>`.

Example Makefile target:

```make
.PHONY: ci-local
ci-local:
	<lint-command>
	<unit-test-command>
```

Adapt to the repository's existing container, virtual environment, or toolchain setup. Do not invent a second toolchain.

## Parity checklist

- Same language/runtime version as the CI image.
- Same dependency lockfile and install command.
- Same database or service versions, if checks need them.
- Same environment variables for tests, with safe local defaults.
- Same test selection and failure behavior.
- Same working directory and path assumptions.

If local parity is not achievable, do not claim the local run replaces the remote check.

## What must stay remote

- Merge-blocking checks that branch protection requires.
- Security, license, and compliance scans.
- Checks that need production-like infrastructure or secrets.
- Build and publish steps.
- Deploy, migration, and release jobs.
- A thin smoke suite that proves the pushed commit works in a clean environment.

Moving these local is a policy change, not a performance optimization.

## Rollout

1. Measure the current remote p50/p90 and the new local command's runtime.
2. Document the command in the README and agent instructions.
3. Keep the remote required check until the local command has proven parity for a reasonable period.
4. Only then consider removing duplicate remote jobs, and only with owner approval.
5. Re-measure after the change; confirm the gate still catches regressions.

## Risks

- **Environment drift:** local tools diverge from CI images.
- **Developer variance:** different CPUs, OSes, and container runtimes.
- **Hidden dependencies:** local services or credentials that CI does not have.
- **Secret leakage:** local scripts reading production credentials.
- **False confidence:** "works on my machine" becomes the merge criterion.
- **Compliance:** moving a required scan out of the gate can violate policy.

Mitigate with container/runtime parity, documented versions, synthetic test data, and a thin remote check.

## Agent instructions

When a repository adopts a local-first loop, add a short instruction such as:

```markdown
Before pushing, run `make ci-local`. Do not push if it fails. The remote pipeline is
the final gate, not the first place to discover lint or unit-test failures.
```

Keep it in the same place the agent already reads project instructions (for example `AGENTS.md`, `CONTRIBUTING.md`, or the repository README).
