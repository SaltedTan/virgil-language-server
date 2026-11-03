# 0016. Performance budgets

- Status: Accepted
- Date: 2026-11-03
- Discussion: [#345](https://github.com/SaltedTan/virgil-language-server/issues/345)

## Context

The [roadmap](../../ROADMAP.md#v10-is-ready-when) makes defined performance budgets a v1.0 requirement: budgets based on published benchmark fixtures that pass in CI or scheduled runs. The proposal in [performance-budgets.md](../performance-budgets.md) recorded measured baselines and candidate targets for the three benchmark fixtures (small, util, Aeneas) on the reference host, with every number traceable to a raw capture. It came from [#345](https://github.com/SaltedTan/virgil-language-server/issues/345), and its numbers were re-measured at revision `139db84582d7f230bd28e3609d2699a200878fe1` in [PR #452](https://github.com/SaltedTan/virgil-language-server/pull/452). The document's [adoption decision table](../performance-budgets.md#adoption-decision-table) left the adoption to the maintainer.

## Decision

On 2026-11-03 the maintainer adopted the proposal in [performance-budgets.md](../performance-budgets.md) exactly as proposed, with every row of its adoption decision table and its proposed values unchanged:

- **Exact numeric rows:** the target tables at revision `139db84582d7f230bd28e3609d2699a200878fe1` on the i7-14700KF reference host, CPU 12.
- **Platforms:** Linux x86-64 only; macOS/Rosetta unmeasured.
- **Noise and sample policy:** 30 measured operations per cell, p50/p95, no trimming; a second run before any failure is declared.
- **Memory envelope:** server 256 MiB HWM, worker 256 MiB HWM, 512 MiB largest simultaneous total.
- **Enforcement mode:** advisory; no CI gate enabled.
- **Exception and retest authority:** the maintainer.

The failure and retest procedure and the baseline-change procedure in [performance-budgets.md](../performance-budgets.md) are adopted with it. That document is the source of the target numbers; this ADR does not copy them.

What the adoption does not cover:

- No CI gate is enabled, and no workflow, test setting, heap, or code changes with it.
- The thresholds of [#347](https://github.com/SaltedTan/virgil-language-server/issues/347) (a >5% per-scenario allocation increase, new worker collections in clean analyses, the <2 MiB semispace-headroom warning, scenario exclusions, and its baseline-migration rules) are not in the adopted table. #347 may now be implemented and calibrated, but enabling its gate needs the maintainer's separate approval of its resource-policy file.
- The v1.0 readiness clause for performance budgets still needs a run of a candidate checked against the adopted table; no candidate has been checked yet.

## Consequences

- Enforcement is advisory on the reference host only: the calibrated absolute checks are meaningful only on the i7-14700KF reference host (CPU 12, pinned, warm file caches, the recorded build and corpus), and no check is enabled by this decision.
- A baseline change needs the recorded baseline-change procedure: a recorded decision by the maintainer, a new capture with the same settings at the new revision, and an update to the document's evidence and baseline tables.
- macOS/Rosetta remains unmeasured, so there is no macOS target and the cross-platform qualification is incomplete.
- The gate of [#347](https://github.com/SaltedTan/virgil-language-server/issues/347) needs its own approval of its resource-policy file; it is not part of this adoption.
- Changes to workflows, dependencies, or suite-wide test settings that would enable a gate need a human merge.

## Alternatives considered

- **Enable a CI gate with the adoption.** The proposal set enforcement to advisory. Its [Enforcement](../performance-budgets.md#enforcement) section finds the absolute checks meaningful only on the reference host, and records no evidence that supports a timing gate on the hosted runners of the scheduled comparison.
