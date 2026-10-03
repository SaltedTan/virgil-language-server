# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0

VIRGIL ?= vendor/virgil
BUILD  ?= build
V3C    := scripts/v3c.sh

# Aeneas front end plus the libraries it depends on, as listed in the pinned
# checkout's aeneas/DEPS. lib/test is excluded here and added only to tests.
AENEAS_DEPS = $(filter-out lib/test/%,$(shell cat $(VIRGIL)/aeneas/DEPS 2>/dev/null))
AENEAS_SRC  = $(VIRGIL)/aeneas/src/*/*.v3 $(addprefix $(VIRGIL)/,$(AENEAS_DEPS))
# Virgil libraries the server uses that Aeneas does not.
VIRGIL_LIBS = $(VIRGIL)/lib/file/json/JsonParser.v3

# The host target, as scripts/v3c.sh selects it. Exported so that every
# compilation uses the same one. src/os/<target>/ holds its system calls.
ifndef V3C_TARGET
V3C_TARGET := $(shell scripts/v3c.sh -print-target 2>/dev/null)
endif
export V3C_TARGET
OS_SRC      = $(sort $(wildcard src/os/$(V3C_TARGET)/*.v3))

# src/main.v3 and src/worker/WorkerMain.v3 hold the entry points of the server
# and the analysis worker, and must be passed first.
ENTRY_SRC   = src/main.v3 src/worker/WorkerMain.v3
SERVER_LIB  = $(filter-out $(ENTRY_SRC) src/os/%,$(shell find src -name '*.v3' | sort)) $(OS_SRC)
# The worker's heap, chosen from measurements in
# docs/decisions/0004-analysis-worker-process.md. The server keeps the default.
WORKER_HEAP ?= 384m
# test/unit/main.v3 holds the test entry point and must be passed first.
TEST_SRC    = test/unit/main.v3 $(filter-out test/unit/main.v3,$(shell find test/unit -name '*.v3' | sort))
BUILDINFO   = $(BUILD)/gen/BuildInfo.v3

.PHONY: all test test-nvim check-virgil buildinfo project-fixtures clean

all: $(BUILD)/virgil-lsp $(BUILD)/virgil-lsp-worker

check-virgil:
	@test -f $(VIRGIL)/aeneas/DEPS || { \
	  echo "error: Virgil sources not found at $(VIRGIL)"; \
	  echo "       run: git submodule update --init --recursive"; exit 1; }

buildinfo: check-virgil
	@mkdir -p $(BUILD)/gen
	@scripts/gen-buildinfo.sh $(BUILDINFO)

$(BUILD)/virgil-lsp: buildinfo src/main.v3 $(SERVER_LIB)
	$(V3C) -output=$(BUILD) -program-name=virgil-lsp \
	  src/main.v3 $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS)

# The server starts this executable from its own directory.
$(BUILD)/virgil-lsp-worker: buildinfo src/worker/WorkerMain.v3 $(SERVER_LIB)
	$(V3C) -heap-size=$(WORKER_HEAP) -output=$(BUILD) -program-name=virgil-lsp-worker \
	  src/worker/WorkerMain.v3 $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS)

project-fixtures:
	python3 test/fixtures/projects/prepare.py $(BUILD)

$(BUILD)/unit-tests: buildinfo project-fixtures $(TEST_SRC) $(SERVER_LIB)
	$(V3C) -output=$(BUILD) -program-name=unit-tests \
	  $(TEST_SRC) $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS) $(VIRGIL)/lib/test/*.v3

# A separate process is essential: prior analyses could hide the initial static
# cache roots in the heap baseline. The probe also accepts source-file arguments.
$(BUILD)/retain-probe: buildinfo test/analysis/RetainProbe.v3 $(SERVER_LIB)
	$(V3C) -output=$(BUILD) -program-name=retain-probe \
	  test/analysis/RetainProbe.v3 $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS)

$(BUILD)/type-depth-probe: buildinfo test/analysis/TypeDepthProbe.v3 $(SERVER_LIB)
	$(V3C) -heap-size=1g -output=$(BUILD) -program-name=type-depth-probe \
	  test/analysis/TypeDepthProbe.v3 $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS)

# Unit and CLI tests start the worker next to their executables.
test: $(BUILD)/virgil-lsp $(BUILD)/virgil-lsp-worker $(BUILD)/unit-tests $(BUILD)/retain-probe $(BUILD)/type-depth-probe
	$(BUILD)/retain-probe
	$(BUILD)/type-depth-probe
	$(BUILD)/unit-tests
	python3 test/fixtures/projects/test_cleanup.py
	test/cli/run.sh $(BUILD)/virgil-lsp
	test/protocol/run.sh $(BUILD)/virgil-lsp
	python3 test/protocol/worker.py $(BUILD)/virgil-lsp
	test/e2e/nvim/run.sh $(BUILD)/virgil-lsp

# Optional locally; CI sets REQUIRE_NVIM=1 so a missing/old editor fails.
test-nvim: $(BUILD)/virgil-lsp $(BUILD)/virgil-lsp-worker
	test/e2e/nvim/run.sh $(BUILD)/virgil-lsp

clean:
	python3 test/fixtures/projects/prepare.py $(BUILD) --cleanup
	rm -rf $(BUILD)
