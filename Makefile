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

# src/main.v3 holds the entry point and must be passed first.
SERVER_LIB  = $(filter-out src/main.v3,$(shell find src -name '*.v3' | sort))
# test/unit/main.v3 holds the test entry point and must be passed first.
TEST_SRC    = test/unit/main.v3 $(filter-out test/unit/main.v3,$(shell find test/unit -name '*.v3' | sort))
BUILDINFO   = $(BUILD)/gen/BuildInfo.v3

.PHONY: all test check-virgil buildinfo clean

all: $(BUILD)/virgil-lsp

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

$(BUILD)/unit-tests: buildinfo $(TEST_SRC) $(SERVER_LIB)
	$(V3C) -output=$(BUILD) -program-name=unit-tests \
	  $(TEST_SRC) $(SERVER_LIB) $(BUILDINFO) $(AENEAS_SRC) $(VIRGIL_LIBS) $(VIRGIL)/lib/test/*.v3

test: $(BUILD)/virgil-lsp $(BUILD)/unit-tests
	$(BUILD)/unit-tests
	test/cli/run.sh $(BUILD)/virgil-lsp

clean:
	rm -rf $(BUILD)
