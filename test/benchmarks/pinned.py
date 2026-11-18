#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Pin guard for benchmark inventory tests."""
import sys


def skip_away_from_pin(case, corpus):
    """Skip the calling test unless vendor/virgil is the revision in the index."""
    gitlink, checkout = corpus.revisions()
    if checkout != gitlink:
        reason = (f"the committed inventory belongs to the pinned Virgil {gitlink}; "
                  f"vendor/virgil is checked out at {checkout}")
        # Default unittest output omits skip reasons.
        print(reason, file=sys.stderr)
        case.skipTest(reason)
