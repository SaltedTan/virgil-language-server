# Architecture decision records

Short records of durable technical decisions. Each one states the context, the decision, and its consequences. A record isn't edited once it's accepted. A later ADR supersedes it instead.

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-virgil-native-server.md) | Implement the server in Virgil | Accepted; in-process whole-program analysis superseded by [0004](0004-analysis-worker-process.md) |
| [0002](0002-pinned-virgil-adapter-boundary.md) | Pin Virgil and isolate it behind an adapter | Accepted |
| [0003](0003-supported-platforms.md) | Supported platforms | Accepted |
| [0004](0004-analysis-worker-process.md) | Run whole-program analysis in a replaceable worker process | Accepted |
| [0005](0005-project-file-format.md) | Project file format, version 1 | Accepted |
| [0006](0006-incomplete-code-repair.md) | Semantic context for completion in incomplete code | Accepted; amended by [0009](0009-current-text-repair.md) and [0013](0013-recover-other-files-in-query-analyses.md) |
| [0007](0007-bundled-virgil-library.md) | Bundled Virgil library and root precedence | Accepted |
| [0008](0008-method-body-fallback.md) | Reanalyze last-parseable text for contained method-body edits | Accepted; amended by [0009](0009-current-text-repair.md) and [0013](0013-recover-other-files-in-query-analyses.md) |
| [0009](0009-current-text-repair.md) | Analyze a broken open file from a bounded repair of its current text | Accepted; amended by [0010](0010-analyze-unrepairable-files.md) and [0013](0013-recover-other-files-in-query-analyses.md) |
| [0010](0010-analyze-unrepairable-files.md) | Analyze files that bounded repair can't fix | Accepted; amends [0009](0009-current-text-repair.md) |
| [0011](0011-declaration-comment-spans.md) | Record declaration comment spans in the analysis result | Accepted |
| [0012](0012-project-configuration-stability.md) | Version 1 project configuration stability | Accepted; builds on [0005](0005-project-file-format.md) |
| [0013](0013-recover-other-files-in-query-analyses.md) | Recover other open files in completion and signature-help analyses | Accepted; amends [0006](0006-incomplete-code-repair.md), [0008](0008-method-body-fallback.md), and [0009](0009-current-text-repair.md) |
| [0014](0014-compatibility-and-support-policy.md) | Supported Virgil, library, and editor combinations | Accepted |

To add one, copy [0000-template.md](0000-template.md) to the next number and open a pull request.
