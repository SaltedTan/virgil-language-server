#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# One-time (idempotent) GitHub repository setup: settings, labels, milestones,
# the initial issue backlog, and branch protection for main.
#
# Usage: scripts/github-setup.sh [owner/repo] [--issues]
# Requires an authenticated `gh` with admin rights on the repository.
set -euo pipefail

REPO=${1:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}
CREATE_ISSUES=0
[ "${2:-}" = "--issues" ] && CREATE_ISSUES=1
CI_CHECK="Build and test (pinned Virgil, linux x86-64)"

echo "== Repository settings: $REPO"
gh repo edit "$REPO" \
    --description "Unofficial Language Server Protocol implementation for the Virgil programming language" \
    --enable-issues --enable-discussions --enable-wiki=false \
    --delete-branch-on-merge --enable-squash-merge --enable-merge-commit=false --enable-rebase-merge \
    --add-topic virgil --add-topic lsp --add-topic language-server --add-topic neovim --add-topic vscode
gh api -X PUT "repos/$REPO/private-vulnerability-reporting" > /dev/null

echo "== Labels"
label() { gh label create "$1" --repo "$REPO" --color "$2" --description "$3" --force > /dev/null; echo "  $1"; }
label "area/protocol"     1d76db "JSON-RPC framing, message model, lifecycle"
label "area/documents"    1d76db "Document store, URIs, position mapping"
label "area/analyzer"     1d76db "Aeneas adapter, snapshots, symbol indexes"
label "area/workspace"    1d76db "Project configuration and workspace model"
label "area/vscode"       5319e7 "VS Code extension"
label "area/neovim"       5319e7 "Neovim / LazyVim integration"
label "area/release"      5319e7 "Packaging, CI, and releases"
label "area/docs"         0e8a16 "Documentation"
label "area/community"    0e8a16 "Community health and contribution process"
label "dependency/virgil" fbca04 "Pinned Virgil revision or upstream compiler changes"
label "type/bug"          d73a4a "Something is not working"
label "type/feature"      a2eeef "New capability or improvement"
label "type/refactor"     c5def5 "Internal restructuring without behaviour change"
label "type/docs"         c5def5 "Documentation-only change"
label "type/test"         c5def5 "Tests and fixtures"
label "good first issue"  7057ff "Good for newcomers"
label "help wanted"       008672 "Extra attention is welcome"
label "blocked/dependency" b60205 "Waiting on another issue or upstream"
label "breaking"          b60205 "Breaking change to configuration or behaviour"
label "performance"       f9d0c4 "Latency, memory, or throughput"
# Remove GitHub's defaults that duplicate the scheme above.
for l in bug enhancement documentation duplicate invalid question wontfix; do
    gh label delete "$l" --repo "$REPO" --yes > /dev/null 2>&1 || true
done

echo "== Milestones"
milestone() {
    local title=$1 desc=$2
    if gh api "repos/$REPO/milestones?state=all&per_page=100" -q '.[].title' | grep -qxF "$title"; then
        echo "  $title (exists)"
    else
        gh api "repos/$REPO/milestones" -f title="$title" -f description="$desc" > /dev/null
        echo "  $title"
    fi
}
milestone "protocol-foundation"    "M0-M1: repository foundation, compiler spike, correct JSON-RPC/LSP core"
milestone "diagnostics-slice"      "M2-M3: document store, position mapping, syntax and semantic diagnostics"
milestone "semantic-navigation"    "M4: symbol indexes, definition, hover"
milestone "v0.1-alpha"             "First tagged prerelease for LazyVim and VS Code"
milestone "rich-language-features" "M5: references, rename, signature help, completion"
milestone "v1.0"                   "Stable configuration, compatibility policy, and performance budgets"

if [ $CREATE_ISSUES -eq 1 ]; then
    echo "== Initial backlog"
    issue() {
        local title=$1 milestone=$2 labels=$3 body=$4
        if gh issue list --repo "$REPO" --state all --search "in:title \"$title\"" --json title -q '.[].title' | grep -qxF "$title"; then
            echo "  exists: $title"
        else
            gh issue create --repo "$REPO" --title "$title" --milestone "$milestone" --label "$labels" --body "$body" > /dev/null
            echo "  created: $title"
        fi
    }
    issue "Compiler spike: parse, verify two files, and follow one binding" protocol-foundation "area/analyzer,type/feature" \
"Extend \`AeneasAdapter\` from single-file parsing to a fresh \`Program\` that runs \`Compilation.parse()\` and \`verify()\` only.

- [ ] Parse one file and verify a two-file program from in-memory bytes
- [ ] Return structured semantic errors as \`AnalysisDiagnostic\`
- [ ] Follow one \`VarExpr.varbind\` to its declaration and report its range
- [ ] Unit tests for the adapter boundary; no initialization or codegen runs

See ROADMAP.md (M0) and ADR-0002."
    issue "JSON-RPC envelope model that preserves request IDs" protocol-foundation "area/protocol,type/feature" \
"Typed model for requests, notifications, responses, and error responses.

- [ ] Integer and string IDs are echoed exactly; notifications get no response
- [ ] \`params\` may be absent, an object, an array, or null
- [ ] Unknown requests get \`MethodNotFound\` with the original ID; unknown notifications are ignored
- [ ] Pending-request table for server-initiated requests
- [ ] Unit tests for each message shape"
    issue "Harden stdio framing and enforce protocol-only stdout" protocol-foundation "area/protocol,type/feature" \
"- [ ] \`Content-Length\` is a byte count; partial reads/writes and fragmented messages are handled
- [ ] Optional \`Content-Type\` header with a charset is accepted
- [ ] Configurable message-size ceiling
- [ ] All logging goes to stderr; a test asserts stdout contains only framed messages"
    issue "Golden transcript tests for requests, notifications, lifecycle, and malformed packets" protocol-foundation "area/protocol,type/test" \
"Transcript-driven tests (input bytes -> expected output bytes) under \`test/protocol/\`.

- [ ] Lifecycle: pre-initialize request, duplicate initialize, shutdown then exit, abnormal exit
- [ ] Fragmented, consecutive, malformed, Unicode, and oversized messages
- [ ] \`\$/cancelRequest\`
- [ ] Runs under \`make test\` and CI"
    issue "Versioned full-text document synchronization" diagnostics-slice "area/documents,type/feature" \
"\`didOpen\`/\`didChange\`/\`didSave\`/\`didClose\` with \`TextDocumentSyncKind.Full\`, URI normalization, and in-memory overlays that take precedence over disk. Stale versions are rejected."
    issue "PositionMap: UTF-8 bytes, compiler columns, and LSP positions" diagnostics-slice "area/documents,type/feature" \
"Per-document map between UTF-8 byte offsets, Aeneas line/tab-expanded display columns, and negotiated LSP positions (UTF-16 default).

Fixtures: tabs, CRLF, non-ASCII BMP, astral characters (surrogate pairs), empty lines, EOF, zero-length and multiline ranges. Round-trip property tests."
    issue "Publish parser diagnostics for unsaved single files" diagnostics-slice "area/analyzer,type/feature" \
"Run \`AeneasAdapter.parseFile\` on the open overlay after each change and publish \`textDocument/publishDiagnostics\`, clearing stale diagnostics on fix and close."
    issue "Document symbols from the VST" diagnostics-slice "area/analyzer,type/feature" \
"\`textDocument/documentSymbol\` with hierarchy and complete ranges for components, classes, enums, layouts, packings, methods, and fields. \`apps/vctags\` in Virgil is a useful model."
fi

echo "== Branch protection: main"
if gh api "repos/$REPO/branches/main" > /dev/null 2>&1; then
    gh api -X PUT "repos/$REPO/branches/main/protection" --input - > /dev/null <<JSON
{
  "required_status_checks": { "strict": true, "contexts": ["$CI_CHECK"] },
  "enforce_admins": false,
  "required_pull_request_reviews": { "required_approving_review_count": 0 },
  "required_conversation_resolution": true,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
    echo "  main: PRs required, CI required, no force-push or deletion"
else
    echo "  main does not exist yet; push it first and re-run" >&2
fi
echo "Done. Create the GitHub Project board in the web UI if wanted."
