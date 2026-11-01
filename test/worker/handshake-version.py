#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""A fake analysis worker whose handshake reports a different server version.

The supervisor must refuse it; see test/unit/AnalysisSupervisorTest.v3.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_handshake import serve

serve("version")
