#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Skip checks of committed data that only holds at the pinned Virgil revision.

The advisory Virgil-master workflow checks `vendor/virgil` out at upstream
`master` and runs the suites. An inventory generated from the pinned Virgil's
sources cannot match another revision, so its check skips there and names both
revisions instead of reporting breakage.
"""
import sys


def skip_away_from_pin(case, corpus):
    """Skip the calling test unless vendor/virgil is the revision in the index."""
    gitlink, checkout = corpus.revisions()
    if checkout != gitlink:
        reason = (f"the committed inventory belongs to the pinned Virgil {gitlink}; "
                  f"vendor/virgil is checked out at {checkout}")
        print(reason, file=sys.stderr)
        case.skipTest(reason)
