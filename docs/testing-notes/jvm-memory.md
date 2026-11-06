# JVM memory and worker restarts on Apple Silicon (2026-11-05)

This note records the measurements for [#463](https://github.com/SaltedTan/virgil-language-server/issues/463): the JVM build's startup, first and warm analyses, per-process peak memory, and the cost of the worker that replaces one after 100 analyses, on an Apple M4 without Rosetta. It ends with a **proposal** for the darwin-arm64 launch flags and memory envelope. The proposal awaits the maintainer's decision. Nothing here changes `scripts/v3c.sh`, `WORKER_HEAP`, the Linux budgets in [performance-budgets.md](../performance-budgets.md), or [ADR-0016](../decisions/0016-performance-budgets.md), and Linux figures below are not Apple Silicon measurements.

**Summary.**

- With today's launchers (`-Xss16m`; uncapped server heap; worker `-Xmx384m`, G1), the Aeneas project peaked at 371.7 MiB server and 376.5 MiB worker resident set size (high-water marks), 748.2 MiB combined. That is half the issue's 1.5 GiB candidate. The worker's `-Xmx384m` doesn't cap its resident size, and the uncapped server holds about 25 MiB of live data in a heap it lets grow past 200 MiB.
- Capping the server at `-Xmx128m` lowered its Aeneas peak to 224.2–246.5 MiB with no measurable cost. Serial GC in the server changed little more.
- Serial GC in the **worker** made warm Aeneas analyses slower than G1 in every run that compared them ([how the note weighs that](#noise-and-repeated-comparisons)), and lowered the worker's peak only through a smaller heap: at 384m its Aeneas peak (381.5 MiB) matched G1's (376.5 MiB), while G1 shrank its heap between analyses and Serial kept at least its 256 MiB initial size committed. A G1 worker at `-Xmx256m` was as fast as at 384m ([G1 worker comparison](#g1-worker-comparison)).
- The worker restart after 100 analyses costs one cold worker: the next Aeneas analysis took 923 ms against 266 ms warm, and analyses stayed slower for at least four more. Averaged over 100 analyses that is about 12 ms (5%) per Aeneas analysis. The live-heap restart never fired: an idle worker holds about 10 MiB, against limits of 31 or 48 MiB.
- `-Xss16m` is no longer needed: on the default thread stack the protocol, CLI, diagnostics, and deep-start suites pass on Linux (1 MiB), and the deep-nesting cases and deep-start test pass on macOS arm64 (2 MiB).
- The JVM build cannot expand project-file globs on macOS on any JDK tried, because the runtime requires `SecureDirectoryStream`, which macOS JDKs don't provide. That blocked the editor scenarios of `scripts/bench-lsp.py` here; see [Limits of this run](#limits-of-this-run).
- **Proposed, for the maintainer's decision:** server `-Xmx128m -XX:+UseSerialGC`, worker `-Xmx256m` (G1), no `-Xss`, and an empirical 1 GiB combined envelope (384 MiB server, 640 MiB worker), about 1.6× the largest combined peak measured with those flags, 625.2 MiB. See [Proposal](#proposal-awaiting-the-maintainers-decision).

## Host, runtimes, and builds

**Host:** the maintainer's Apple M4 laptop (4 performance and 6 efficiency cores, 16 GiB), macOS 27.0.1, Python 3.9.6. Runs were unpinned (macOS has no `taskset`) and alternated configurations in blocks. The 1-minute load average was between 3.2 and 5.8 at every block boundary of the main runs, and 13.7 at the start of the later OpenJDK 23 rerun, with no `Runner.Worker` process active at any boundary (the laptop also hosts GitHub Actions runners). A latency difference under about 20% in a single comparison is treated as noise, the heuristic of the [2026-11-03 Apple M4 check](https://github.com/SaltedTan/virgil-language-server/issues/191); [Noise and repeated comparisons](#noise-and-repeated-comparisons) gives the repeated evidence for the two smaller differences this note relies on.

**Runtimes** (all arm64):

| Name | Runtime | Unpacked, apparent / allocated | `tar.gz` | Used for |
| --- | --- | ---: | ---: | --- |
| rt21 | `jlink --add-modules java.base --strip-debug --no-header-files --no-man-pages --compress=zip-9` from Oracle Java SE 21.0.5 (`21.0.5+9-LTS-239`) | 29.4 / 29.8 MiB | 15.9 MiB | Every measurement unless stated |
| rt21-cds | The same, plus `--generate-cds-archive` | 54.3 / 54.7 MiB | 22.8 MiB | Startup only |
| OpenJDK 23.0.2 | The full JDK the 2026-11-03 check used | | | Startup and restart-boundary cross-checks |

Sizes are in MiB (2^20 bytes), from `fix1/runtime-sizes.txt`, which records the commands and byte counts. *Apparent* is the sum of the files' sizes (30,803,516 and 56,985,148 bytes), *allocated* is `du -sk`, and `tar.gz` is the byte count of `tar czf - .` in the runtime's directory. The runtimes were linked again, with the same commands, to record their sizes after the measurements.

The vendor and version are [#464](https://github.com/SaltedTan/virgil-language-server/issues/464)'s decision; Java SE 21.0.5 was simply the arm64 JDK 21 installed on the host. A Linux cross-check used Ubuntu's OpenJDK 25.0.4.1 on the x86-64 reference host.

**No Rosetta:** each runtime's `java` is `Mach-O 64-bit executable arm64` with `os.arch` `aarch64`, and `scripts/jvm-memory.py` read the kernel's `P_TRANSLATED` flag for every server and worker JVM it saw: all 354 were native. As a negative control, the same check reported `arch -x86_64 /bin/sleep` as translated, and a native `/bin/sleep` and its own Python process as not (`fix1/rosetta-control.txt`).

**Builds:** `make jvm` on Linux at `5d5da316` (BuildInfo revision `v0.1.0-alpha.6-10-g5d5da316ffdd`, Virgil `b86f43f1`), copied to the Mac: `virgil-lsp.jar` SHA-256 `36f51786be0719d36533c04099057a01044cd3dc6bc3f4e26ae7c15305077dcd`, `virgil-lsp-worker.jar` `2c318f1d5b6bf5058a7e890cacd35e15ffc9136fa058b588e45c530749c6e5a6`. The corpus came from a checkout at the same revision.

## Method

[`scripts/jvm-memory.py`](../../scripts/jvm-memory.py) writes scratch copies of the two launchers for each configuration, with other JVM flags or another `java`, and drives them through `virgil-lsp/analyze` with the benchmark corpus's explicit file lists:

- **Restart boundary:** one server per configuration and project, 105 analyses with the default limit of 100, so analysis 101 lands on a fresh worker. *Warm* is analyses 81–100. The server's log gives each replacement's reason.
- **Cold and second analyses** (`--cold`): fresh process pairs, timing their first analysis (both JVMs cold) and their second.
- **Startup** (`--startup`): process start to the `initialize` reply, as the probe's `startup.py` measured it.
- **High-water marks:** each JVM runs under a waiter that collects its peak when it ends. On macOS the waiter is `/usr/bin/time -l`, which reports the **maximum resident set size** (`ru_maxrss`, including clean file-backed pages such as `libjvm` and the jars) and the **peak memory footprint** (the kernel's lifetime peak `phys_footprint`, which excludes clean file-backed pages; Activity Monitor's *Memory* column shows the current footprint). On Linux the waiter reports `ru_maxrss`, the same as `VmHWM`. The server kills a retired worker's launcher, the waiter's parent, so the worker ends on its closed request pipe and the waiter still reports. These are true peaks, not samples.
- **Flags in effect:** `-XX:+PrintFlagsFinal` for each configuration, stored with the results.
- **Native memory** (`--nmt`): `-XX:NativeMemoryTracking=summary` and `jcmd <pid> VM.native_memory summary` (from the JDK 21 install, for the jlinked runtime) at analysis 19 of 20, when both processes are idle and warm.

The waiter adds about 120 ms to each worker start (Python's startup and the fork), so latencies come from runs without waiters (`--no-wrap`) and peaks from runs with them. That includes the OpenJDK 23 cross-check, which was rerun without waiters for its latencies; its first run, with them, is used only for its peaks. Combined peaks add the server's peak to the largest worker peak; the two may occur at different times, so the sum is an upper bound.

**Configurations.** Each step changes one thing from the one before, apart from the last; [a follow-up](#g1-worker-comparison) compared two more:

| Name | Server flags | Worker flags | Why |
| --- | --- | --- | --- |
| `today` | `-Xss16m` | `-Xss16m -Xmx384m` | The launchers that `make jvm` writes; G1 by default |
| `cap` | `-Xss16m -Xmx128m` | `-Xss16m -Xmx384m` | Cap the server, about 5× its 25 MiB live set |
| `serial` | `-Xss16m -Xmx128m -XX:+UseSerialGC` | `-Xss16m -Xmx384m -XX:+UseSerialGC` | Serial GC, which the probe's compiler runs used |
| `proposal` | `-Xmx128m -XX:+UseSerialGC` | `-Xmx256m -XX:+UseSerialGC` | Default stack, and the worker at about 2× its live peak |

An exploratory search first found each process's live heap from `-Xlog:gc` (in the raw results' `explore/`): with Serial GC, the worker's heap after collections during an Aeneas analysis reached 107–115 MiB, and in short runs warm Aeneas analyses slowed from about 470 ms at `-Xmx192m` and above to 615 ms at 160m and 753 ms at 128m. The server's heap after collections was 17–25 MiB. After the forced collection that follows each analysis, an idle worker holds about 10 MiB.

### Noise and repeated comparisons

The 20% rule is a heuristic for one isolated comparison on this unpinned, shared laptop. Two conclusions rest on smaller latency differences, and on repetition instead:

- **G1 against Serial in the worker.** Five separate runs compared warm Aeneas analyses with a Serial and a G1 worker, alternating configurations in blocks: `boundary-plain`, `boundary-hwm`, `g1-plain`, `g1-hwm`, and `fix1/boundary-jdk23-plain` on OpenJDK 23. In all 14 pairings of a Serial-worker and a G1-worker configuration within the same run, the Serial median was higher, by 8–24%, and in 12 of them its p10 was above the G1 configuration's p90. Cold first analyses (`cold.json`, 30 each) were 11.5–14.1% slower with Serial in all four pairings. The note treats the direction as established and its size, about 15%, as approximate.
- **A CDS archive at startup.** One run (`startup.json`) alternated eight configurations, 20 launches each. The four without a CDS archive had medians of 168.1–169.7 ms, and the four with one (rt21-cds and the full OpenJDK 23) 148.3–149.8 ms, 18.3–21.4 ms apart in every pairing. That is a single run, so the 20 ms saving is tentative; its direction is what CDS exists for.

Other latency differences under 20% in this note are reported as no difference, or marked tentative where they are mentioned.

**Raw results** are in `~/virgil-jvm-463/results/` on the Mac and copied to `/home/sean-tan/.t3/scratch-virgil/jvm-463/results/` on the reference host, as `mac/` and `linux/`; they are not committed. Each table names its file, under `mac/` unless it says `linux/`. The commands are in [Reproduce](#reproduce).

## Results

### Startup

Process start to the `initialize` reply, 20 alternating runs each, in milliseconds (`startup.json`):

| Configuration | Runtime | Median (p10–p90) |
| --- | --- | ---: |
| `today` | rt21 | 168.1 (165.9–186.1) |
| `cap` | rt21 | 168.2 (166.1–176.9) |
| `serial` | rt21 | 169.7 (165.3–183.2) |
| `proposal` | rt21 | 168.3 (165.0–185.6) |
| `today` | rt21-cds | 149.4 (146.2–158.3) |
| `proposal` | rt21-cds | 148.3 (146.5–177.9) |
| `today` | OpenJDK 23.0.2 | 148.7 (147.4–181.9) |
| `proposal` | OpenJDK 23.0.2 | 149.8 (146.8–163.2) |

The flags don't change startup. A jlinked runtime has no class-data sharing (CDS) archive unless `jlink` is given `--generate-cds-archive`, and without one each JVM started about 20 ms (12%) slower in this run, a tentative size ([Noise and repeated comparisons](#noise-and-repeated-comparisons)). The 2026-11-03 check measured 159.0 ms on OpenJDK 23.

### First and second analyses

Fresh process pairs, 6 blocks of 5 each, in milliseconds (`cold.json`). *First* includes starting the worker:

| Project | Configuration | First | Second |
| --- | --- | ---: | ---: |
| small | `today` | 179.9 (175.9–240.4) | 3.9 (3.7–4.5) |
| small | `cap` | 179.6 (176.3–224.8) | 3.8 (3.7–4.5) |
| small | `serial` | 177.1 (175.4–233.7) | 3.2 (3.1–4.4) |
| small | `proposal` | 179.7 (174.9–210.6) | 3.2 (3.1–4.1) |
| util | `today` | 289.7 (284.5–332.4) | 53.1 (50.3–66.6) |
| util | `cap` | 292.9 (282.7–335.9) | 52.8 (50.4–73.3) |
| util | `serial` | 290.2 (283.1–339.7) | 48.3 (46.3–66.8) |
| util | `proposal` | 288.9 (281.9–372.9) | 50.0 (46.5–73.8) |
| Aeneas | `today` | 999.6 (937.9–1232.7) | 592.3 (518.7–704.1) |
| Aeneas | `cap` | 1009.3 (934.2–1282.9) | 586.8 (483.8–694.6) |
| Aeneas | `serial` | 1125.8 (1073.6–1398.4) | 644.9 (569.7–825.3) |
| Aeneas | `proposal` | 1140.2 (1038.4–1508.0) | 639.8 (531.3–783.3) |

### Warm analyses and the restart boundary

One server per block, configuration, and project, 3 blocks, without waiters (`boundary-plain.json`). Warm is the median (p10–p90) of analyses 81–100 over all blocks; the other columns list each block's value, in milliseconds:

| Project | Configuration | Analysis 1 | Warm 81–100 | Analysis 101 (fresh worker) | 102 | 103 | Median of 104–105 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| util | `today` | 319, 329, 299 | 20.1 (17.8–22.3) | 276, 281, 281 | 47, 50, 54 | 35, 38, 40 | 29, 29, 29 |
| util | `cap` | 325, 311, 303 | 19.9 (17.0–23.1) | 273, 305, 281 | 43, 50, 47 | 36, 36, 36 | 29, 29, 29 |
| util | `serial` | 605, 320, 303 | 16.1 (14.5–18.8) | 279, 290, 270 | 41, 48, 46 | 33, 34, 34 | 25, 28, 25 |
| util | `proposal` | 315, 311, 302 | 15.5 (14.7–18.2) | 272, 288, 266 | 41, 46, 42 | 31, 34, 34 | 26, 35, 26 |
| Aeneas | `today` | 1035, 999, 946 | 266.3 (262.5–278.9) | 923, 896, 963 | 509, 453, 590 | 365, 372, 498 | 361, 336, 442 |
| Aeneas | `cap` | 990, 1070, 951 | 271.3 (261.8–298.9) | 877, 1052, 897 | 508, 572, 533 | 369, 533, 446 | 370, 427, 351 |
| Aeneas | `serial` | 1034, 1060, 1097 | 321.3 (311.2–336.9) | 1009, 1205, 1068 | 540, 771, 699 | 443, 556, 592 | 423, 401, 430 |
| Aeneas | `proposal` | 1105, 1212, 1190 | 316.3 (306.9–365.4) | 1104, 1503, 1059 | 639, 723, 529 | 567, 526, 503 | 562, 430, 392 |

Every replacement was `after 100 analyses (limit 100)`. A fresh worker pays the JVM's start and the JIT's warm-up again, while the server stays warm: analysis 101 cost about as much as analysis 1, and warm-up continued past analysis 105. For `today`, analyses 101–105 together took about 1.2 s more than five warm ones on Aeneas and about 0.33 s more on util, using the medians above. Spread over the 100 analyses of a worker's life, that is about 12 ms (5%) per Aeneas analysis and 3.3 ms (16%) per util analysis, before any warm-up after 105. A larger limit would spread the same cost thinner. The limit protects against compiler state that grows across analyses; this run measures its cost, not whether 100 is still the right count.

The OpenJDK 23 cross-check ran without waiters, with rt21's `today` as a control in the same run (`fix1/boundary-jdk23-plain.json`, 3 blocks, jars byte-identical to the others). Spotlight indexing and other activity on the laptop put the load average at 13.7 when it started and 5.7 when it ended. In milliseconds, as in the table above:

| Project | Configuration | Warm 81–100 | Analysis 101 (fresh worker) |
| --- | --- | ---: | ---: |
| util | `today`, rt21 control | 20.7 (17.4–23.1) | 266, 267, 287 |
| util | `today`, OpenJDK 23 | 20.4 (17.2–24.4) | 236, 237, 220 |
| util | `proposal`, OpenJDK 23 | 15.5 (14.4–18.5) | 232, 231, 229 |
| util | `g1w256`, OpenJDK 23 | 21.2 (18.2–23.2) | 230, 229, 249 |
| Aeneas | `today`, rt21 control | 262.7 (255.5–275.4) | 854, 952, 839 |
| Aeneas | `today`, OpenJDK 23 | 252.0 (235.6–263.4) | 747, 841, 814 |
| Aeneas | `proposal`, OpenJDK 23 | 301.9 (296.9–309.3) | 805, 963, 894 |
| Aeneas | `g1w256`, OpenJDK 23 | 263.4 (251.8–277.4) | 762, 827, 867 |

OpenJDK 23 matched rt21 within noise, and the rt21 control's analysis 101 (839–952 ms) agreed with the earlier run's (896–963 ms). `g1w256` is the [G1 worker comparison](#g1-worker-comparison)'s candidate. An earlier one-block OpenJDK 23 run with waiters (`boundary-jdk23.json`) is used only for its peaks below, since its restart timings include the waiter.

### The live-heap restart

The worker also retires when its live heap after the forced collection reaches an eighth of its maximum heap. On the JVM both figures are approximate: `Runtime.maxMemory()` and `totalMemory() - freeMemory()` after `System.gc()`. At analysis 19 of an Aeneas run, `jcmd GC.heap_info` showed 9.8 MiB used under G1 and 11.2 MiB under Serial (2.7 MiB young, 8.5 MiB old), against limits of 48 MiB at `-Xmx384m` and about 31 MiB at `-Xmx256m` with Serial, whose `maxMemory()` excludes a survivor space (`nmt-rt21.json`). No live-heap replacement occurred in any run: of 236 worker lifetimes, 118 reached their analysis limit (100, or a lower one in the exploratory and native-memory runs) and the rest ended with their server. The approximation therefore has 3–5× headroom on the largest corpus project; an eighth of a 128m heap, 16 MiB, would have been close.

### Peak memory

High-water marks with waiters, 2 blocks (`boundary-hwm.json`), in MiB: maximum resident set size, with the peak memory footprint in parentheses. Each figure is the maximum over blocks and, for the worker, over both workers of a session (4 per cell):

| Project | Configuration | Server | Worker | Combined RSS | Combined footprint |
| --- | --- | ---: | ---: | ---: | ---: |
| small | `today` | 86.8 (54.3) | 63.4 (40.0) | 150.2 | 94.3 |
| small | `cap` | 87.5 (54.3) | 64.1 (38.6) | 151.6 | 92.9 |
| small | `serial` | 81.3 (52.7) | 62.0 (37.9) | 143.3 | 90.6 |
| small | `proposal` | 84.7 (53.0) | 63.3 (38.9) | 148.0 | 91.9 |
| util | `today` | 280.0 (213.6) | 233.9 (125.6) | 513.8 | 339.2 |
| util | `cap` | 201.9 (140.1) | 229.1 (116.3) | 430.9 | 256.4 |
| util | `serial` | 141.4 (94.7) | 169.5 (106.8) | 310.9 | 201.5 |
| util | `proposal` | 134.3 (93.4) | 176.7 (110.0) | 311.0 | 203.4 |
| Aeneas | `today` | 371.7 (312.1) | 376.5 (285.3) | 748.2 | 597.4 |
| Aeneas | `cap` | 246.5 (187.0) | 382.6 (270.1) | 629.1 | 457.2 |
| Aeneas | `serial` | 238.9 (177.6) | 381.5 (280.2) | 620.5 | 457.8 |
| Aeneas | `proposal` | 234.9 (180.3) | 350.9 (285.3) | 585.8 | 465.6 |

With OpenJDK 23 (`boundary-jdk23.json`, one block, with waiters), Aeneas peaked at 466.9 MiB server and 328.2 MiB worker for `today` (795.1 MiB combined), and 214.1 and 325.0 MiB for `proposal` (539.2 MiB). The full JDK maps its CDS archive, which rt21 lacks.

The small project shows the floor: about 85 MiB for the server and 63 MiB for the worker. The server's uncapped default heap is a quarter of the host's memory, 4 GiB here, and G1 sizes its young generation from it, so the server collects rarely and its resident size follows garbage, not live data.

### Where the memory goes

Native memory tracking at analysis 19 of 20 on Aeneas, committed MiB (`nmt-rt21.json`). This is a snapshot of idle processes, not their peaks:

| Configuration | Process | Total | Java heap | Thread | Code | GC | Arena | Metaspace | Other |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `today` | server | 568.1 | 256.0 | 220.6 | 7.8 | 56.3 | 12.5 | 8.6 | 6.2 |
| `today` | worker | 324.4 | 40.0 | 190.4 | 15.9 | 53.5 | 7.9 | 9.8 | 7.0 |
| `cap` | server | 425.6 | 119.0 | 218.6 | 7.7 | 53.1 | 12.5 | 8.6 | 6.2 |
| `cap` | worker | 324.0 | 40.0 | 190.4 | 15.8 | 53.1 | 7.9 | 9.8 | 7.0 |
| `serial` | server | 364.1 | 128.0 | 200.5 | 7.7 | 0.4 | 12.8 | 8.6 | 6.1 |
| `serial` | worker | 483.8 | 256.1 | 168.2 | 15.4 | 0.9 | 26.8 | 9.8 | 6.7 |
| `proposal` | server | 196.2 | 128.0 | 32.5 | 7.7 | 0.4 | 12.9 | 8.6 | 6.1 |
| `proposal` | worker | 323.4 | 256.0 | 28.2 | 15.0 | 0.9 | 6.9 | 9.8 | 6.6 |

*Other* is the class, compiler, symbol, and smaller categories. NMT on macOS counts each thread's whole stack reservation as committed, so the *Thread* column with `-Xss16m` is mostly address space, not resident memory: removing `-Xss16m` changed the server's peak resident size by only a few MiB. G1's own structures take about 55 MiB per process, against under 1 MiB for Serial. Serial kept its heap committed at no less than its initial size, which defaults to a 64th of memory, 256 MiB here, capped at the maximum. That is the whole heap for the 128 MiB server and the 256 MiB `proposal` worker, whose initial and maximum sizes are equal, but not for the `serial` worker: its flags record a 256 MiB initial and 384 MiB maximum heap, and NMT shows 256.1 MiB committed of about 392 MiB reserved. G1 shrank the worker's heap to 40 MiB after each forced collection. A separate run with OpenJDK 23, whose NMT reports peaks, showed the worker's compiler arenas peaking at 69 MiB (`explore/nmt23.json`); with `-XX:TieredStopAtLevel=1` they peaked at 8.5 MiB and the worker's resident peaks fell by 15–55 MiB, but in that single exploratory run warm Aeneas analyses were about 18% slower, so it is not proposed.

Subtracting the maximum heap from a resident peak gives a **residual estimate** of the memory outside the heap (libjvm and the jars' mapped pages, code cache, metaspace, compiler arenas, and touched thread stacks), assuming the whole heap was resident at the peak: about 107 MiB in the `proposal` server (234.9 − 128) and 95 MiB in its worker (350.9 − 256). The resident heap can be smaller than its maximum, so a residual is a lower bound on resident non-heap memory at that moment, not an allowance for it; no resident breakdown at the peak was measured.

### Editor scenarios on Linux

`scripts/bench-lsp.py` couldn't run its project-file scenarios on the Mac (see below), so the same four configurations ran there on the x86-64 reference host with OpenJDK 25.0.4.1, to check that a 128m server holds the editor scenarios: 3 alternating blocks, unpinned within hardware threads 12–15, under the shared benchmark lock (`linux/bench-linux.json`). These are not Apple Silicon figures. Median (p10–p90), in milliseconds:

| Project | Scenario | `today` | `cap` | `serial` | `proposal` |
| --- | --- | ---: | ---: | ---: | ---: |
| Aeneas | `fresh_analysis` | 1340.5 (1191.0–1368.8) | 1290.6 (1142.5–1373.8) | 1309.4 (1187.0–1408.7) | 1332.0 (1221.8–1405.3) |
| Aeneas | `reused_analysis` | 489.1 (375.8–619.7) | 440.4 (353.4–627.2) | 469.0 (376.3–554.6) | 466.0 (415.0–597.1) |
| Aeneas | `semantic_diagnostic` | 539.8 (525.9–561.1) | 548.8 (532.4–593.6) | 529.9 (517.3–543.7) | 552.1 (541.9–568.8) |
| Aeneas | `completion_miss` | 190.2 (160.5–255.6) | 196.6 (148.2–238.8) | 188.6 (180.7–202.9) | 225.6 (214.7–261.2) |
| Aeneas | `prepareRename` | 35.3 (27.8–45.4) | 35.3 (28.7–45.2) | 38.6 (31.7–43.0) | 36.9 (30.3–45.0) |
| Aeneas | `rename` | 391.1 (368.8–424.7) | 400.7 (363.9–435.8) | 387.2 (380.3–407.5) | 414.0 (397.7–426.4) |
| util | `reused_analysis` | 84.5 (66.6–101.2) | 74.7 (56.1–104.2) | 55.5 (36.5–74.2) | 52.2 (40.1–72.9) |
| util | `completion_miss` | 79.2 (59.9–97.4) | 75.9 (60.7–107.1) | 50.0 (43.2–55.4) | 43.0 (29.2–55.7) |
| util | `rename` | 106.2 (82.4–134.2) | 96.8 (79.6–137.3) | 84.9 (71.1–99.1) | 85.1 (59.2–96.1) |

Each cell has 15 samples; the median latencies of hover, definition, references, and completion hits stayed under 5 ms in every configuration. Every semantic check passed. The Aeneas sessions' `VmHWM` at their end peaked at 611.9 MiB server and 403.1 MiB worker for `today`, against 243.4 and 337.0 MiB for `proposal`. The 128m server answered every scenario. Only the Aeneas completion miss, which runs in the worker, was slower for `proposal` than for `serial`, whose worker heap is 384 MiB: 19%, in this one run, so tentative.

### Thread stack

[#461](https://github.com/SaltedTan/virgil-language-server/issues/461) (#472) bounds deep parses for the JVM's default thread stack, which is 1 MiB on x86-64 Linux and 2 MiB on macOS arm64 (`ThreadStackSize`). Without `-Xss16m`:

- Linux, OpenJDK 25: `test/protocol/run.sh` 44/44, `test/cli/run.sh` 66/66, `test/diagnostics/run.py` 28/28, and `make test-jvm`'s deep-start test passed, as they did with it (`linux/suites/`). `test/protocol/worker.py` failed in its 3,000-level chain case both with and without `-Xss16m`: the JVM's chain bound of 2,048 levels stops that parse, which the test doesn't expect yet.
- macOS, rt21: `test/jvm/deepstart.py` passed, and the protocol suite's `diagnostics-deep-nesting` and `diagnostics-long-chains` cases passed. The protocol and CLI suites had the same 12 and 3 other failures with and without `-Xss16m` (`mac/stack/`): the project-file failure below, a `/tmp` that is a symlink on macOS, and the stall worker that the scratch launchers lacked.
- Every Aeneas analysis in this note's `proposal` runs used the default stack.

## Limits of this run

- **Directory enumeration on macOS.** `src/os/jvm/V3S_System.java` opens directories only as a `SecureDirectoryStream` and otherwise returns `ENOTSUP`. OpenJDK 17.0.9, Java SE 21.0.5, and OpenJDK 23.0.2 on this Mac all return a plain `DirectoryStream`, so every project file's source glob fails with "cannot enumerate project directory" and its semantic contexts are disabled. Explicit analyses, as in this note, still work. This needs its own issue before packaging; it isn't a memory question.
- The editor scenarios' memory on macOS is therefore unmeasured; the Linux run stands in for it.
- One host, unpinned, with background load; a single latency difference under about 20% is noise unless [repeated](#noise-and-repeated-comparisons).
- Combined peaks add per-process peaks that may not coincide.

## G1 worker comparison

Serial lost time only in the worker, so a follow-up ran the Serial-capped server with a G1 worker, with `proposal` as the same-run control (`g1-plain.json`, 3 blocks without waiters; `g1-hwm.json`, 2 blocks with them):

| Name | Server flags | Worker flags |
| --- | --- | --- |
| `proposal` | `-Xmx128m -XX:+UseSerialGC` | `-Xmx256m -XX:+UseSerialGC` |
| `g1w256` | `-Xmx128m -XX:+UseSerialGC` | `-Xmx256m` |
| `g1w384` | `-Xmx128m -XX:+UseSerialGC` | `-Xmx384m` |

Latency in milliseconds, as in the restart-boundary table, from the runs without waiters:

| Project | Configuration | Analysis 1 | Warm 81–100 | Analysis 101 (fresh worker) | 102 | 103 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| util | `proposal` | 754, 302, 284 | 14.6 (14.2–17.8) | 261, 263, 257 | 40, 40, 41 | 33, 34, 30 |
| util | `g1w256` | 712, 298, 284 | 19.1 (17.4–21.6) | 259, 264, 300 | 45, 43, 53 | 37, 34, 38 |
| util | `g1w384` | 723, 303, 286 | 19.5 (18.0–21.2) | 261, 270, 263 | 48, 45, 43 | 36, 35, 36 |
| Aeneas | `proposal` | 1123, 1196, 1115 | 304.2 (299.9–314.1) | 1025, 911, 1043 | 665, 548, 614 | 568, 481, 526 |
| Aeneas | `g1w256` | 978, 929, 921 | 264.7 (257.9–275.6) | 878, 944, 853 | 439, 605, 509 | 371, 515, 414 |
| Aeneas | `g1w384` | 965, 1012, 944 | 265.7 (257.8–311.4) | 863, 870, 1270 | 524, 410, 523 | 493, 366, 474 |

Peaks in MiB, as in the peak-memory table:

| Project | Configuration | Server | Worker | Combined RSS | Combined footprint |
| --- | --- | ---: | ---: | ---: | ---: |
| util | `proposal` | 153.1 (98.0) | 206.3 (122.5) | 359.4 | 220.5 |
| util | `g1w256` | 153.5 (94.3) | 224.9 (128.0) | 378.4 | 222.3 |
| util | `g1w384` | 155.5 (97.0) | 241.4 (134.6) | 396.9 | 231.6 |
| Aeneas | `proposal` | 224.2 (185.5) | 353.7 (290.0) | 577.9 | 475.5 |
| Aeneas | `g1w256` | 241.0 (182.5) | 384.3 (275.2) | 625.2 | 457.7 |
| Aeneas | `g1w384` | 238.7 (176.3) | 373.3 (303.5) | 611.9 | 479.7 |

With a G1 worker, warm Aeneas analyses took 264.7 ms against Serial's 304.2 ms, and first analyses and the analysis after a restart were about 100–200 ms faster, at 256m as at 384m. Its resident peak on Aeneas was 20–30 MiB higher than Serial's; at 256m its footprint was lower. Serial was faster on util's small warm analyses, but those take under 20 ms either way. The first util analysis of each configuration's first block (712–754 ms) was the run's first JVM start and is an outlier.

## Proposal (awaiting the maintainer's decision)

This is a proposal for the maintainer to adopt or revise, not an adopted setting; #463's last acceptance item stays open until then.

**Flags** for the darwin-arm64 JVM launchers:

| Process | Flags | Why |
| --- | --- | --- |
| Server | `-Xmx128m -XX:+UseSerialGC` | About 5× its 25 MiB live set. The cap does most of the saving; Serial's collector structures commit under 1 MiB against G1's 53 MiB, though the peaks differed by only about 8 MiB. Neither showed a latency cost in the server's analyses or editor scenarios |
| Worker | `-Xmx256m` (G1, the default) | About 2.2× Aeneas's 107–115 MiB live peak. Serial was slower on warm Aeneas analyses in every one of five runs, by about 15% ([evidence](#noise-and-repeated-comparisons)), and G1 at 256m was as fast as at 384m |
| Both | No `-Xss` | The default stack (2 MiB on macOS arm64) passes every deep-nesting test since #472 |

The native worker's 384 MiB heap is two 192 MiB semispaces, so its live data can reach about 192 MiB; a 256m G1 heap holds a comparable live set, though no corpus project came close enough to test it. The worker's `-Xmx` comes from `WORKER_HEAP` today, which the native build shares, so adopting 256m for the JVM needs a JVM-only setting. Both launchers should also come with a runtime that has a CDS archive ([For packaging](#for-packaging-464)).

**Envelope.** The per-process envelopes are empirical: the largest resident peak measured with the proposed flags, plus a margin for projects larger than the corpus. The heap caps bound only the Java heap; the residual above them is an estimate, as [Where the memory goes](#where-the-memory-goes) explains, so the margin is a judgment, not a derived bound:

| Process | Maximum heap | Residual over the heap (estimate) | Largest peak measured (RSS) | Proposed envelope | Margin |
| --- | ---: | ---: | ---: | ---: | ---: |
| Server | 128 MiB | 96–113 MiB | 241.0 MiB | 384 MiB | 1.59× |
| Worker | 256 MiB | 128.3 MiB with G1; 95–98 MiB with Serial | 384.3 MiB | 640 MiB | 1.67× |
| **Combined** | 384 MiB | | 625.2 MiB | **1 GiB** | 1.64× |

The server's residuals are its peaks minus 128 MiB across the five Serial-capped runs (224.2–241.0 MiB); the worker's are the `g1w256` peak minus 256 MiB, and the Serial `proposal` peaks (350.9 and 353.7 MiB) minus 256 MiB. The issue's **1.5 GiB candidate** would also hold: with these flags the largest combined peak measured on the Mac was 625.2 MiB RSS and 457.7 MiB footprint, and even today's launchers peaked at 748.2 MiB (rt21) and 795.1 MiB (OpenJDK 23) on the Mac, and at 1015.0 MiB with OpenJDK 25 across all editor scenarios on Linux. The proposal is 1 GiB with the flags above because it leaves about 1.6× margin over every peak measured with them; a project that keeps more compiler state, or compiles more code, than Aeneas would use more of that margin, and the corpus can't show how much. 1.5 GiB would remain the envelope if the maintainer keeps today's uncapped server. Either way the envelope is resident set size (`ru_maxrss`) on Apple Silicon, separate from the Linux x86-64 budgets.

**Costs the maintainer should weigh:**

- Startup: about 150 ms per JVM with a CDS archive, against 4.8 ms for the Rosetta build.
- A cold Aeneas analysis about 1 s, and the analysis after each worker restart 0.8–0.9 s, then several more analyses of JIT warm-up: about 5% on average across a worker's 100 analyses.

## For packaging (#464)

- A plain `jlink --add-modules java.base` runtime has no CDS archive; `--generate-cds-archive` saved about 20 ms per JVM start ([Startup](#startup)) and added 25.0 MiB unpacked, exactly its two archives: `classes.jsa` (12,959,744 bytes) and `classes_nocoops.jsa` (13,221,888 bytes). The second serves only JVMs without compressed object pointers, such as heaps over 32 GiB, so a package could drop it. The `tar.gz` grew by 6.9 MiB.
- The launchers would carry the approved flags; each process needs its own `-Xmx`, since the server's default is a quarter of the user's memory.
- The directory-enumeration failure above blocks project files on every macOS JDK tried.
- The JDK 21 `jcmd` attached to the jlinked runtime's processes, so native memory can be inspected in the field with a full JDK of the same version.

## Reproduce

The JSON session field `summary.workers` counts workers started to serve the requested analyses; a retirement after the final analysis does not count an unstarted replacement. In each configuration's `flags`, the server probe inherits the calling environment and the worker probe uses an empty environment, matching worker launches. Environment options such as `JAVA_TOOL_OPTIONS` therefore affect only the server probe.

On the Mac, with this change's `scripts/jvm-memory.py` copied into a checkout at the measured revision, `vendor/virgil` initialized, the jars that `make jvm` built in `build/jvm-target`, and the runtime in `~/virgil-jvm-463/rt21`:

```sh
J=~/virgil-jvm-463/rt21/bin/java
C=(--config 'today|-Xss16m|-Xss16m -Xmx384m'
   --config 'cap|-Xss16m -Xmx128m|-Xss16m -Xmx384m'
   --config 'serial|-Xss16m -Xmx128m -XX:+UseSerialGC|-Xss16m -Xmx384m -XX:+UseSerialGC'
   --config 'proposal|-Xmx128m -XX:+UseSerialGC|-Xmx256m -XX:+UseSerialGC')
python3 scripts/jvm-memory.py --java "$J" "${C[@]}" --startup-only --startup 20 --output startup.json
python3 scripts/jvm-memory.py --java "$J" "${C[@]}" --no-boundary --cold 5 --blocks 6 \
  --project small --project util --project aeneas --output cold.json
python3 scripts/jvm-memory.py --java "$J" "${C[@]}" --no-wrap --blocks 3 --output boundary-plain.json
python3 scripts/jvm-memory.py --java "$J" "${C[@]}" --blocks 2 \
  --project small --project util --project aeneas --output boundary-hwm.json
python3 scripts/jvm-memory.py --java "$J" "${C[@]}" --nmt --jcmd "$JDK21/bin/jcmd" \
  --project aeneas --max-analyses 20 --analyses 22 --output nmt-rt21.json
G=(--config 'proposal|-Xmx128m -XX:+UseSerialGC|-Xmx256m -XX:+UseSerialGC'
   --config 'g1w256|-Xmx128m -XX:+UseSerialGC|-Xmx256m'
   --config 'g1w384|-Xmx128m -XX:+UseSerialGC|-Xmx384m')
python3 scripts/jvm-memory.py --java "$J" "${G[@]}" --no-wrap --blocks 3 --output g1-plain.json
python3 scripts/jvm-memory.py --java "$J" "${G[@]}" --blocks 2 --output g1-hwm.json
J23=~/Library/Java/JavaVirtualMachines/openjdk-23.0.2/Contents/Home/bin/java
python3 scripts/jvm-memory.py --java "$J23" --no-wrap --blocks 3 \
  --config "today-rt21|-Xss16m|-Xss16m -Xmx384m|$J" \
  --config 'today-jdk23|-Xss16m|-Xss16m -Xmx384m' \
  --config 'proposal-jdk23|-Xmx128m -XX:+UseSerialGC|-Xmx256m -XX:+UseSerialGC' \
  --config 'g1w256-jdk23|-Xmx128m -XX:+UseSerialGC|-Xmx256m' --output boundary-jdk23-plain.json
```

`$JDK21` is the JDK the runtime was linked from; `fix1/runtime-sizes.txt` records the `jlink` commands for rt21 and rt21-cds. The startup run also took `|JAVA` configurations for rt21-cds and OpenJDK 23. `python3 scripts/jvm-memory.py --smoke` checks the script itself in a few seconds, on Linux or macOS, after `make jvm`.
