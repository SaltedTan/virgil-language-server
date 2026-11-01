# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""The handshake of a fake analysis worker, for the supervisor's unit tests.

The real worker's first frame reports the protocol version, server version,
and source revision of its build (AnalysisWire.encodeHello). These scripts
send the same frame with one of those fields changed, so that each of the
supervisor's matching checks in AnalysisSupervisor is exercised on its own.
The supervisor must refuse the worker. See test/unit/AnalysisSupervisorTest.v3.

The unit tests run from the repository root, so the server's build identity
comes from the generated build/gen/BuildInfo.v3 and its protocol version from
src/worker/AnalysisWire.v3, the same sources the server was built from.
"""
import os
import re
import struct
import time

HELLO = 1
HEAP_BYTES = 0
# The worker's result descriptor (AnalysisWire.RESULT_FD).
RESULT_FD = 4


def sleb(value):
    """Encodes {value} as signed LEB128."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        last = (value == 0 and not byte & 0x40) or (value == -1 and byte & 0x40)
        if not last:
            byte |= 0x80
        out.append(byte)
        if last:
            return bytes(out)


def string(text):
    data = text.encode()
    return sleb(len(data)) + data


def server_identity():
    """The protocol version, server version, and source revision the server
    was built with, as (protocol, version, revision)."""
    buildinfo = open("build/gen/BuildInfo.v3", encoding="utf-8").read()
    version = re.search(r'def VERSION = "([^"]*)";', buildinfo).group(1)
    revision = re.search(r'def REVISION = "([^"]*)";', buildinfo).group(1)
    wire = open("src/worker/AnalysisWire.v3", encoding="utf-8").read()
    protocol = int(re.search(r"def PROTOCOL_VERSION = (\d+);", wire).group(1))
    return protocol, version, revision


def frame(field, protocol, version, revision):
    """The handshake frame whose {field} differs from the server's build."""
    if field == "protocol":
        protocol -= 1
    if field == "version":
        version = "0.0.0-test"
    if field == "revision":
        revision = "test-revision"
    payload = bytes([HELLO]) + sleb(protocol) + string(version) + string(revision) + sleb(HEAP_BYTES)
    return struct.pack("<I", len(payload)) + payload


def serve(field):
    """Writes the handshake to the result pipe, then blocks until killed."""
    os.write(RESULT_FD, frame(field, *server_identity()))
    while True:
        time.sleep(3600)
