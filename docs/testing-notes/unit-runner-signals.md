# The unit runner's count probe and macOS signals

This note records the diagnosis for [#336](https://github.com/SaltedTan/virgil-language-server/issues/336): why `RunTest.test_signals_at_launch_and_probe_reaping_leave_no_children` failed with two different signatures in macOS CI, and whether the fix that followed both cases, [PR #288](https://github.com/SaltedTan/virgil-language-server/pull/288) (`27c57e97`), resolves them. It is a record, not a supported command.

**Conclusion:** the first failure is proven to be macOS reporting `EPERM` from `killpg` on the count probe's process group, which the runner of 2026-10-25 didn't handle ([CI 40169702035](https://github.com/SaltedTan/virgil-language-server/actions/runs/40169702035)). The second failure ([CI 40261185342](https://github.com/SaltedTan/virgil-language-server/actions/runs/40261185342)) is consistent with the same `EPERM`, but its log can't prove it: by 2026-10-26 `main` discarded any exception once a signal was recorded, so the run shows only a probe that was never reaped, not which exception left `total` early. `27c57e97` stops the probe directly when `killpg` is refused and then reaps it, which removes both signatures for the `EPERM` path. Two cases in `test/unit/test_run.py` now pin that path with injected process state. No production runner change is made here, and the test hasn't failed on macOS since the fix (see [Recurrence](#recurrence)); the other exits that could still skip the reap are listed under [What isn't proven](#what-isnt-proven).

## The two recorded failures

| Run | Head | Runner at that commit | Signature |
| --- | --- | --- | --- |
| [40169702035](https://github.com/SaltedTan/virgil-language-server/actions/runs/40169702035) | `dde709d5` | One process at a time, no `PermissionError` handler, `main` lets exceptions out | `ERROR` with `PermissionError: [Errno 1] Operation not permitted` from `os.killpg(process.pid, signal.SIGKILL)` in `total`'s cleanup, for `boundary='probe-launch'` and all three signals |
| [40261185342](https://github.com/SaltedTan/virgil-language-server/actions/runs/40261185342) | `11baf9d6` | Groups in slots, `main` catches `BaseException` when a signal is recorded, `total` re-raises `EPERM` when `poll()` is `None` | `FAIL: AssertionError: ChildProcessError not raised` at `self.assertRaises(ChildProcessError)`, for `boundary='probe-launch'`, `signal=SIGHUP` |

Both runs are before `27c57e97`, which merged on 2026-10-26 at 03:16 UTC.

## How `EPERM` gives both signatures

`total` starts the count probe (`[copy, "*"]`) in a session of its own, reads its `##>N unit tests` line, and then kills the probe's process group with `killpg` before waiting for it. On macOS that `killpg` can report `EPERM` while the group is going away, whichever signal brought the run down.

- At `dde709d5` the cleanup was `except ProcessLookupError: pass` and then `process.wait()`, and `main` had no exception handling, so `EPERM` left `total` and reached the test as an error: the first signature. **Proof:** the traceback in the run log shows the `killpg` call and the mock wrapper only, with no assertion.
- Between the two runs, #273 and #276 rewrote the runner to per-group processes and slots, and `main` gained `except BaseException: if not children.signum: raise`. An `EPERM` at `11baf9d6` would therefore have raised inside `total`, been discarded by `main` because the signal was recorded, and left the probe unreaped: `os.waitpid(probe.pid, os.WNOHANG)` then returns instead of raising, which is the second signature. At `11baf9d6` the `poll()`-based handler re-raised in exactly that state, since the killed probe was not yet waitable. **Proof** that this path gives the second signature: the injected case below. **Not proven:** that this path is the one the 2026-10-26 run took.
- `27c57e97` replaced that handler with `process.kill()` and an unconditional `process.wait()`, so a refused `killpg` no longer leaves `total` before the probe is stopped and reaped. **Proof:** with the `poll()`-based handler restored, the new cases and the `probe-exiting` boundary fail with each signature; with `27c57e97` they pass.

## What isn't proven

- That macOS returns `EPERM` from `killpg` only while the group leader is a zombie or still exiting, and that the 2026-10-26 run's `SIGHUP` reached the runner on the same interleaving. No macOS machine was available for this diagnosis, so the timing of the real `EPERM` is inferred from the two logs and from the injected state that reproduces their assertions.
- That no other exception left `total` in the 2026-10-26 run. On `main`, two exits can still skip `process.wait()` if their call raises: `Children.add` runs `process.kill()` when a signal is already recorded, before `total` enters the `try` that reaps the probe; and the signal handler, `Children.stop`, runs `process.kill()` on every child and could raise from inside `total`'s cleanup, between `killpg` and `wait()`. `Popen.kill` polls first and ignores `ProcessLookupError`, so either would need another error, such as `EPERM` from `kill` itself. Neither has been observed, and no recorded run points at them.

## Reproducing it without macOS

This machine is Ubuntu 24.04 on x86-64, and this note's evidence is local and deterministic. The tests inject the state the macOS runs showed, with the stand-in executable the file already uses: no sleeps, no retries, and real child processes, so the no-child assertion still runs against a real pid.

`test/unit/test_run.py` gained two cases:

- `test_probe_cleanup_stops_the_probe_directly_when_killpg_is_refused` replaces `os.killpg` with a raise of `EPERM` while the probe is still inside its 60 seconds, and checks that `total` returns the count, that the probe's status is `-SIGKILL` (the runner killed it, not the group), that `os.waitpid` raises `ChildProcessError`, that `Children.processes` is empty, and that the count's tree is gone.
- `test_a_recorded_signal_does_not_hide_a_probe_cleanup_failure` sends `SIGHUP` as the probe starts, makes `EPERM` the answer to every `killpg`, and makes `poll()` report `None` for the probe, which is the 2026-10-26 state. It checks `128 + SIGHUP`, that the probe was reaped, and that the fixtures keep the modes they had.

Both run with the rest of the file:

```sh
python3 test/unit/test_run.py
```

The controls replace `test/unit/run.py`'s `except PermissionError` clause with each earlier form and run that file again, then restore it with `git checkout -- test/unit/run.py`: `process.kill()` becomes `if process.poll() is None: raise` for `11baf9d6`, and the whole clause is removed for `dde709d5`.

| Runner form | `test_probe_cleanup_stops_the_probe_directly_when_killpg_is_refused` | `test_a_recorded_signal_does_not_hide_a_probe_cleanup_failure` | Existing `probe-exiting` boundary |
| --- | --- | --- | --- |
| `dde709d5`, no `PermissionError` handler | error: `PermissionError: [Errno 1] Operation not permitted` | fail: `AssertionError: ChildProcessError not raised` | fail, same assertion |
| `11baf9d6`, re-raise when `poll()` is `None` | error: `PermissionError: [Errno 1] Operation not permitted` | fail: `AssertionError: ChildProcessError not raised` | fail, same assertion |
| `main`, whose cleanup is `27c57e97`'s | pass | pass | pass |

With `27c57e97` in place, `python3 test/unit/test_run.py` passed on every run of the investigation, and `make test` passed on 2026-11-01: 1,094 unit tests in 68 groups, and 43 suites with none failing.

## Recurrence

Searched with `gh run list --workflow ci.yml --limit 200`, which reaches back to 2026-10-25T05:52Z, and kept the 149 runs created at or after `27c57e97`. Six of those failed. Five had a failed macOS job, and none of their failed logs mentions `test_signals_at_launch_and_probe_reaping_leave_no_children`; all five report `== python3 test/unit/test_run.py: passed`, and their failures are other suites (`test/e2e/test_nvim_results.py`, `test/protocol/test_source_mutations.py`, `test/e2e/nvim/run.sh`, `test/benchmarks/test_report.py`, and two Linux editor suites). The sixth, `40360070689`, failed only on Linux. So the test has not failed on macOS since the fix, in the 149 runs available; runs whose macOS job was skipped say nothing either way.
