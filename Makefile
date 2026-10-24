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
# BENCH=1 is for a separate build directory only. Normal compilation removes
# BenchProbe and all its calls. RiGc.stats counts bytes without verbose output.
BENCH ?= 0
ifeq ($(BENCH),1)
BENCH_FLAGS = -redef-field=BenchProbe.enabled=true,RiGc.stats=true
endif
# test/unit/main.v3 holds the test entry point and must be passed first.
TEST_SRC    = test/unit/main.v3 $(filter-out test/unit/main.v3,$(shell find test/unit -name '*.v3' | sort))
BUILDINFO   = $(BUILD)/gen/BuildInfo.v3
# The command run-unit-tests wraps. A plain = lets a command-line override work,
# and test_cleanup.py uses that to exercise the wrapper without the real tests.
UNIT_TESTS  = $(BUILD)/unit-tests

.PHONY: all test test-unit run-unit-tests test-nvim test-lazyvim check-virgil buildinfo project-fixtures print-worker-heap clean

all: $(BUILD)/virgil-lsp $(BUILD)/virgil-lsp-worker $(BUILD)/benchmark-build.json

$(BUILD)/benchmark-build.json: $(BUILD)/virgil-lsp $(BUILD)/virgil-lsp-worker
	python3 scripts/bench-build-info.py $(BUILD) $(WORKER_HEAP) $(BENCH) $(V3C_TARGET) \
	  $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS)

# One command builds the opt-in probes and runs all corpus/scenario blocks.
.PHONY: bench bench-build
bench-build:
	$(MAKE) BUILD=$(BUILD)/bench BENCH=1 all

bench: bench-build
	python3 scripts/bench-lsp.py --exe $(BUILD)/bench/virgil-lsp --require-probes $(BENCH_ARGS)

check-virgil:
	@test -f $(VIRGIL)/aeneas/DEPS || { \
	  echo "error: Virgil sources not found at $(VIRGIL)"; \
	  echo "       run: git submodule update --init --recursive"; exit 1; }

buildinfo: check-virgil
	@mkdir -p $(BUILD)/gen
	@scripts/gen-buildinfo.sh $(BUILDINFO)

$(BUILD)/virgil-lsp: buildinfo src/main.v3 $(SERVER_LIB)
	$(V3C) $(BENCH_FLAGS) -output=$(BUILD) -program-name=virgil-lsp \
	  src/main.v3 $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS)

# The server starts this executable from its own directory.
$(BUILD)/virgil-lsp-worker: buildinfo src/worker/WorkerMain.v3 $(SERVER_LIB)
	$(V3C) $(BENCH_FLAGS) -heap-size=$(WORKER_HEAP) -output=$(BUILD) -program-name=virgil-lsp-worker \
	  src/worker/WorkerMain.v3 $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS)

project-fixtures:
	python3 test/fixtures/projects/prepare.py $(BUILD)

$(BUILD)/unit-tests: buildinfo project-fixtures $(TEST_SRC) $(SERVER_LIB)
	$(V3C) $(BENCH_FLAGS) -output=$(BUILD) -program-name=unit-tests \
	  $(TEST_SRC) $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS) $(VIRGIL)/lib/test/*.v3

# A separate process is essential: prior analyses could hide the initial static
# cache roots in the heap baseline. The probe also accepts source-file arguments.
$(BUILD)/retain-probe: buildinfo test/analysis/RetainProbe.v3 $(SERVER_LIB)
	$(V3C) $(BENCH_FLAGS) -output=$(BUILD) -program-name=retain-probe \
	  test/analysis/RetainProbe.v3 $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS)

$(BUILD)/type-depth-probe: buildinfo test/analysis/TypeDepthProbe.v3 $(SERVER_LIB)
	$(V3C) -heap-size=1g -output=$(BUILD) -program-name=type-depth-probe \
	  test/analysis/TypeDepthProbe.v3 $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS)

# The unit tests need two fixture directories to be unreadable. --run applies
# that for the test process only and restores it however the process ends, so
# the build directory stays removable. set -f keeps ARGS patterns away from the
# shell's pathname expansion while word splitting still separates several of
# them. Pass a glob with ARGS, e.g. `make test-unit ARGS='ProjectExpansion:*'`.
run-unit-tests:
	set -f; python3 test/fixtures/projects/prepare.py $(BUILD) --run $(UNIT_TESTS) $(ARGS)

test-unit: $(BUILD)/unit-tests $(BUILD)/virgil-lsp-worker
	$(MAKE) --no-print-directory run-unit-tests

# Unit and CLI tests start the worker next to their executables.
test: $(BUILD)/virgil-lsp $(BUILD)/virgil-lsp-worker $(BUILD)/unit-tests $(BUILD)/retain-probe $(BUILD)/type-depth-probe $(BUILD)/benchmark-build.json
	$(BUILD)/retain-probe
	$(BUILD)/type-depth-probe
	$(MAKE) --no-print-directory run-unit-tests ARGS=
	python3 test/fixtures/projects/test_cleanup.py
	python3 test/e2e/test_coverage.py
	python3 test/e2e/test_editor_results.py
	python3 test/e2e/test_editor_results_render.py
	test/cli/run.sh $(BUILD)/virgil-lsp
	test/protocol/run.sh $(BUILD)/virgil-lsp
	python3 test/diagnostics/run.py $(BUILD)/virgil-lsp
	python3 test/protocol/worker.py $(BUILD)/virgil-lsp
	python3 test/protocol/coordinates.py $(BUILD)/virgil-lsp
	python3 test/protocol/fingerprints.py $(BUILD)/virgil-lsp
	python3 test/protocol/bundled_library.py $(BUILD)/virgil-lsp
	python3 test/benchmarks/test_client.py
	python3 scripts/bench-lsp.py --smoke --exe $(BUILD)/virgil-lsp --output /dev/null
	test/release/run.sh
	test/e2e/nvim/run.sh $(BUILD)/virgil-lsp

# Optional locally; CI sets REQUIRE_NVIM=1 so a missing/old editor fails.
test-nvim: $(BUILD)/virgil-lsp $(BUILD)/virgil-lsp-worker
	test/e2e/nvim/run.sh $(BUILD)/virgil-lsp

# Not part of `make test`: needs tmux and GitHub access, and takes about a
# minute. CI runs it in a job of its own (test/e2e/lazyvim/README.md#ci).
test-lazyvim: $(BUILD)/virgil-lsp $(BUILD)/virgil-lsp-worker
	test/e2e/lazyvim/run.sh $(BUILD)/virgil-lsp

# scripts/package-release.sh records the worker heap in its build manifest.
print-worker-heap:
	@echo '$(WORKER_HEAP)'

clean:
	python3 test/fixtures/projects/prepare.py $(BUILD) --cleanup
	rm -rf $(BUILD)
