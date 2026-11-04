# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0

VIRGIL ?= vendor/virgil
# Host detection never selects the JVM target; V3C_TARGET=jvm does, and then
# builds into a directory of its own by default. See "The JVM target" below.
ifeq ($(V3C_TARGET),jvm)
BUILD  ?= build/jvm-target
else
BUILD  ?= build
endif
V3C    := scripts/v3c.sh
# Flags for every executable. The pinned runtime's unrolled GC clearing and
# copying loops still benefit from the global register allocator at the default
# optimization level. See docs/decisions/0015-gc-global-register-allocation.md.
V3C_FLAGS = '-global-regalloc=RiGc.*'

# Offline protocol shape budget; includes mandatory semantic/lifecycle oracles.
PROTOCOL_SHAPES_SEED  ?= 0
PROTOCOL_SHAPES_CASES ?= 2000
PROTOCOL_SHAPES = python3 test/protocol/shapes.py $(BUILD)/virgil-lsp --seed $(PROTOCOL_SHAPES_SEED) \
	--cases $(PROTOCOL_SHAPES_CASES) --artifacts $(BUILD)/protocol-shapes

# Opt-in seeded source mutations, outside `make test`; see test/protocol/README.md.
SOURCE_MUTATIONS_SEED  ?= 20261028
SOURCE_MUTATIONS_CASES ?= 100

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

# The JVM target's runtime class and the JDK that compiles it and runs the
# jars. JVM_DEPS is a prerequisite of every executable, and empty for native
# targets, which need no JDK.
JAVA        ?= java
JAVAC       ?= $(JAVA) -m jdk.compiler/com.sun.tools.javac.Main
JAVAC_FLAGS  = -source 8 -target 8 -encoding UTF-8 -Xlint:all -Xlint:-options -Werror
JVM_RT_SRC   = $(sort $(wildcard src/os/jvm/*.java))
JVM_RT       = $(BUILD)/jvm/rt
# The JVM target's own build directory when a native one builds it.
JVM_BUILD   ?= $(BUILD)/jvm-target
ifeq ($(V3C_TARGET),jvm)
JVM_DEPS     = $(JVM_RT)/V3S_System.class
JVM_BUILD    = $(BUILD)
export JVM_RT JAVA
ifeq ($(BENCH),1)
$(error BENCH=1 needs a native target: the JVM target has no collection counts or allocation totals for the benchmark probes)
endif
endif

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
# How many unit-test groups run at once; empty means one for each CPU, up to 8.
UNIT_JOBS  ?=
# How many other suites `make test` runs at once; empty means one for each CPU,
# up to 4.
SUITE_JOBS ?=

# The suites `make test` runs after the unit tests, through test/suites.py,
# each a quoted shell command. The longest on macOS CI come first, so that the
# short ones fill in at the end.
SUITES = \
	'test/cli/run.sh $(BUILD)/virgil-lsp' \
	'python3 test/protocol/worker.py $(BUILD)/virgil-lsp' \
	'python3 test/e2e/clean-install/test_install.py' \
	'$(PROTOCOL_SHAPES)' \
	'python3 test/fixtures/projects/test_cleanup.py' \
	'test/release/run.sh' \
	'python3 test/e2e/test_editor_results_render.py' \
	'$(BUILD)/type-depth-probe' \
	'python3 test/e2e/test_coverage.py' \
	'python3 test/e2e/test_editor_results.py' \
	'python3 test/e2e/test_nvim_results.py $(BUILD)/virgil-lsp' \
	'python3 scripts/bench-tolerance.py --smoke --exe $(BUILD)/virgil-lsp --output /dev/null' \
	'test/protocol/run.sh $(BUILD)/virgil-lsp' \
	'python3 test/unit/test_run.py' \
	'python3 test/test_suites.py' \
	'python3 test/diagnostics/run.py $(BUILD)/virgil-lsp' \
	'python3 test/protocol/fingerprints.py $(BUILD)/virgil-lsp' \
	'python3 test/benchmarks/test_report.py' \
	'python3 test/protocol/bundled_library.py $(BUILD)/virgil-lsp' \
	'python3 test/protocol/body_fallback.py $(BUILD)/virgil-lsp' \
	'python3 test/protocol/capacity_warning.py $(BUILD)/virgil-lsp' \
	'python3 test/protocol/current_repair.py $(BUILD)/virgil-lsp' \
	'python3 scripts/bench-lsp.py --smoke --exe $(BUILD)/virgil-lsp --output /dev/null' \
	'test/e2e/nvim/run.sh $(BUILD)/virgil-lsp' \
	'python3 test/protocol/coordinates.py $(BUILD)/virgil-lsp' \
	'python3 test/benchmarks/test_client.py' \
	'python3 test/protocol/type_definition.py $(BUILD)/virgil-lsp' \
	'python3 test/protocol/inlay_hints.py $(BUILD)/virgil-lsp' \
	'python3 test/protocol/semantic_tokens.py $(BUILD)/virgil-lsp' \
	'python3 test/protocol/implementation.py $(BUILD)/virgil-lsp' \
	'python3 test/protocol/type_hierarchy.py $(BUILD)/virgil-lsp' \
	'python3 test/protocol/folding_ranges.py $(BUILD)/virgil-lsp' \
	'python3 test/protocol/selection_ranges.py $(BUILD)/virgil-lsp' \
	'python3 test/protocol/call_hierarchy.py $(BUILD)/virgil-lsp' \
	'python3 test/protocol/code_actions.py $(BUILD)/virgil-lsp' \
	'python3 test/protocol/document_symbol_budget.py $(BUILD)/virgil-lsp' \
	'python3 test/protocol/outline_parse_budget.py $(BUILD)/virgil-lsp' \
	'python3 test/benchmarks/test_tolerance.py' \
	'python3 test/protocol/test_reply_shapes.py' \
	'python3 test/protocol/test_source_mutations.py' \
	'python3 test/protocol/client_initialization.py $(BUILD)/virgil-lsp' \
	'python3 test/protocol/first_project.py $(BUILD)/virgil-lsp' \
	'$(BUILD)/retain-probe' \
	'python3 test/e2e/clean-install/test_readme.py'

.PHONY: all test test-protocol-shapes test-source-mutations test-unit run-unit-tests test-nvim test-lazyvim check-virgil buildinfo project-fixtures print-worker-heap clean

all: $(BUILD)/virgil-lsp $(BUILD)/virgil-lsp-worker

$(BUILD)/benchmark-build.json: $(BUILD)/virgil-lsp $(BUILD)/virgil-lsp-worker
	python3 scripts/bench-build-info.py $(BUILD) $(WORKER_HEAP) $(BENCH) $(V3C_TARGET) $(V3C_FLAGS) -- \
	  $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS)

# One command builds the opt-in probes and runs all corpus/scenario blocks.
.PHONY: bench bench-build bench-metadata
bench-metadata: $(BUILD)/benchmark-build.json

bench-build:
	$(MAKE) BUILD=$(BUILD)/bench BENCH=1 all bench-metadata

bench: bench-build
	python3 scripts/bench-lsp.py --exe $(BUILD)/bench/virgil-lsp --require-probes $(BENCH_ARGS)

check-virgil:
	@test -f $(VIRGIL)/aeneas/DEPS || { \
	  echo "error: Virgil sources not found at $(VIRGIL)"; \
	  echo "       run: git submodule update --init --recursive"; exit 1; }

buildinfo: check-virgil
	@mkdir -p $(BUILD)/gen
	@scripts/gen-buildinfo.sh $(BUILDINFO)

$(BUILD)/virgil-lsp: buildinfo src/main.v3 $(SERVER_LIB) $(JVM_DEPS)
	$(V3C) $(V3C_FLAGS) $(BENCH_FLAGS) -output=$(BUILD) -program-name=virgil-lsp \
	  src/main.v3 $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS)

# The server starts this executable from its own directory.
$(BUILD)/virgil-lsp-worker: buildinfo src/worker/WorkerMain.v3 $(SERVER_LIB) $(JVM_DEPS)
	$(V3C) $(V3C_FLAGS) $(BENCH_FLAGS) -heap-size=$(WORKER_HEAP) -output=$(BUILD) -program-name=virgil-lsp-worker \
	  src/worker/WorkerMain.v3 $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS)

# The real worker, with deterministic stalls enabled only for tests. Share the
# normal worker's sources, heap and build info so the server accepts its hello.
$(BUILD)/stall/virgil-lsp-worker: buildinfo src/worker/WorkerMain.v3 $(SERVER_LIB) $(JVM_DEPS)
	@mkdir -p $(BUILD)/stall
	$(V3C) $(V3C_FLAGS) $(BENCH_FLAGS) -redef-field=WorkerStall.enabled=true \
	  -heap-size=$(WORKER_HEAP) -output=$(BUILD)/stall -program-name=virgil-lsp-worker \
	  src/worker/WorkerMain.v3 $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS)

project-fixtures:
	python3 test/fixtures/projects/prepare.py $(BUILD)

$(BUILD)/unit-tests: buildinfo project-fixtures $(TEST_SRC) $(SERVER_LIB) $(JVM_DEPS)
	$(V3C) $(V3C_FLAGS) $(BENCH_FLAGS) -output=$(BUILD) -program-name=unit-tests \
	  $(TEST_SRC) $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS) $(VIRGIL)/lib/test/*.v3

# A separate process is essential: prior analyses could hide the initial static
# cache roots in the heap baseline. The probe also accepts source-file arguments.
$(BUILD)/retain-probe: buildinfo test/analysis/RetainProbe.v3 $(SERVER_LIB) $(JVM_DEPS)
	$(V3C) $(V3C_FLAGS) $(BENCH_FLAGS) -output=$(BUILD) -program-name=retain-probe \
	  test/analysis/RetainProbe.v3 $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS)

$(BUILD)/type-depth-probe: buildinfo test/analysis/TypeDepthProbe.v3 $(SERVER_LIB) $(JVM_DEPS)
	$(V3C) $(V3C_FLAGS) -heap-size=1g -output=$(BUILD) -program-name=type-depth-probe \
	  test/analysis/TypeDepthProbe.v3 $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS)

# The unit tests need two fixture directories to be unreadable. test/unit/run.py
# applies that for the test processes only and restores it however they end, so
# the build directory stays removable. Without ARGS, it runs each group of tests
# in a process of its own, UNIT_JOBS at once, and lists the slowest tests. set -f
# keeps ARGS patterns away from the shell's pathname expansion while word
# splitting still separates several of them. Pass a glob with ARGS, e.g.
# `make test-unit ARGS='ProjectExpansion:*'`, to run the matches in one process.
run-unit-tests:
	set -f; python3 test/unit/run.py $(if $(UNIT_JOBS),--jobs=$(UNIT_JOBS)) $(BUILD) $(UNIT_TESTS) $(ARGS)

test-unit: $(BUILD)/unit-tests $(BUILD)/virgil-lsp-worker $(BUILD)/stall/virgil-lsp-worker
	$(MAKE) --no-print-directory run-unit-tests

# Unit and CLI tests start the worker next to their executables. The unit tests
# run first, since they use every CPU themselves.
test: $(BUILD)/virgil-lsp $(BUILD)/virgil-lsp-worker $(BUILD)/stall/virgil-lsp-worker $(BUILD)/unit-tests $(BUILD)/retain-probe $(BUILD)/type-depth-probe
	$(MAKE) --no-print-directory run-unit-tests ARGS=
	python3 test/suites.py $(if $(SUITE_JOBS),--jobs=$(SUITE_JOBS)) $(SUITES)

# Seed and total budget are overridable for wider deterministic local runs.
test-protocol-shapes: $(BUILD)/virgil-lsp $(BUILD)/virgil-lsp-worker
	$(PROTOCOL_SHAPES)

# Not part of `make test` or CI. Pass SOURCE_MUTATIONS_ARGS for other bounds,
# --stress, or --replay CASE.json.
test-source-mutations: $(BUILD)/virgil-lsp $(BUILD)/virgil-lsp-worker
	python3 test/protocol/source_mutations.py $(BUILD)/virgil-lsp --seed $(SOURCE_MUTATIONS_SEED) \
	  --cases $(SOURCE_MUTATIONS_CASES) --artifacts $(BUILD)/source-mutations $(SOURCE_MUTATIONS_ARGS)

# Optional locally; CI sets REQUIRE_NVIM=1 so a missing/old editor fails.
test-nvim: $(BUILD)/virgil-lsp $(BUILD)/virgil-lsp-worker
	test/e2e/nvim/run.sh $(BUILD)/virgil-lsp

# Not part of `make test`: needs tmux and GitHub access, and takes about a
# minute. CI runs it in a job of its own (test/e2e/lazyvim/README.md#ci).
test-lazyvim: $(BUILD)/virgil-lsp $(BUILD)/virgil-lsp-worker
	test/e2e/lazyvim/run.sh $(BUILD)/virgil-lsp

# The JVM target's runtime class, src/os/jvm/V3S_System.java, and its tests.
# Not part of `make test`: they need a JDK, which only the JVM target does.
# The class is built for Java 8, the level of upstream's rt/jvm/Makefile. The
# default JAVAC needs JDK 9 or later; set JAVA or JAVAC to use another JDK.
.PHONY: jvm-runtime test-jvm-runtime jvm test-jvm
# Every class file in $(JVM_RT) belongs in each jar, not only V3S_System.class.
jvm-runtime: $(JVM_RT)/V3S_System.class

$(JVM_RT)/V3S_System.class: $(JVM_RT_SRC)
	rm -rf $(JVM_RT) && mkdir -p $(JVM_RT)
	$(JAVAC) $(JAVAC_FLAGS) -d $(JVM_RT) $(JVM_RT_SRC)

# Pass ARGS to run only the tests whose names contain one of its words.
test-jvm-runtime: jvm-runtime check-virgil
	rm -rf $(BUILD)/jvm/upstream $(BUILD)/jvm/test && mkdir -p $(BUILD)/jvm/upstream $(BUILD)/jvm/test
	$(JAVAC) -source 8 -target 8 -nowarn -Xlint:-options -d $(BUILD)/jvm/upstream $(VIRGIL)/rt/jvm/src/V3S_System.java
	$(JAVAC) $(JAVAC_FLAGS) -cp $(JVM_RT) -d $(BUILD)/jvm/test test/jvm/V3SSystemTest.java
	$(JAVA) -cp $(JVM_RT):$(BUILD)/jvm/test -Djava.io.tmpdir=$${TMPDIR:-/tmp} \
	  -Dv3s.runtime.classes=$(JVM_RT) \
	  -Dv3s.upstream.classes=$(BUILD)/jvm/upstream \
	  V3SSystemTest $(ARGS) </dev/null

# The JVM target, for development: `make jvm` builds the server and worker
# from the same sources and build information as `make`, as jars with
# launchers, in $(JVM_BUILD). `make V3C_TARGET=jvm <target>` builds or runs
# any other target for it, such as `test`, in build/jvm-target by default.
# test-jvm runs the runtime's tests, then starts the JVM server, which starts
# its JVM worker, for an initialize/shutdown exchange. Neither is part of
# `make test`, and both need a JDK.
ifeq ($(V3C_TARGET),jvm)
jvm: all
else
jvm:
	$(MAKE) --no-print-directory V3C_TARGET=jvm BUILD=$(JVM_BUILD) all
endif

test-jvm: test-jvm-runtime jvm
	python3 test/jvm/smoke.py $(JVM_BUILD)/virgil-lsp

# scripts/package-release.sh records the worker heap in its build manifest.
print-worker-heap:
	@echo '$(WORKER_HEAP)'

clean:
	python3 test/fixtures/projects/prepare.py $(BUILD) --cleanup
	rm -rf $(BUILD)
