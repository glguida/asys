.DEFAULT_GOAL := build

PREFIX ?= $(HOME)/.local
DESTDIR ?=
PYTHON ?= python3
NODE ?= node
NPM ?= npm
export PREFIX DESTDIR

JS_PACKAGES := $(patsubst %/package.json,%,$(wildcard asys-inference/components/protocol/*/package.json asys-inference/components/*/package.json)) asys-workers asys-bpmn asys-human-interface

.PHONY: all host install-host build install test test-deps help

all: build

host:
	install -D -m 0755 tools/asys bin/asys

install-host: host
	install -D -m 0755 bin/asys "$(DESTDIR)$(PREFIX)/bin/asys"
	install -d "$(DESTDIR)$(PREFIX)/share/asys/python/asys"
	install -m 0644 python/asys/*.py "$(DESTDIR)$(PREFIX)/share/asys/python/asys/"
	install -d "$(DESTDIR)$(PREFIX)/share/asys/python/asys_runtime"
	install -m 0644 asys-runtime/asys_runtime/*.py "$(DESTDIR)$(PREFIX)/share/asys/python/asys_runtime/"
	install -m 0644 LICENSE "$(DESTDIR)$(PREFIX)/share/asys/"
	install -m 0644 asys-runtime/LICENSE.multiagent "$(DESTDIR)$(PREFIX)/share/asys/"

build: host
	$(MAKE) -C asys-inference build
	$(MAKE) -C asys-bpmn build
	$(MAKE) -C asys-oneshot host
	$(MAKE) -C asys-human-interface build

install:
	$(MAKE) -C asys-inference install
	$(MAKE) -C asys-bpmn install
	$(MAKE) -C asys-oneshot install-host
	$(MAKE) -C asys-human-interface install

test-deps:
	@set -e; for directory in $(JS_PACKAGES); do $(NPM) --prefix "$$directory" ci --ignore-scripts; done
	$(MAKE) -C asys-human-interface host-deps

test:
	$(NODE) --test --test-concurrency=1 'test/*.test.mjs'
	$(MAKE) -C asys-inference test
	cd asys-runtime && $(PYTHON) -m unittest discover -s test
	$(NODE) --test --test-concurrency=1 'asys-runtime/test/*.test.mjs'
	$(MAKE) -C asys-oneshot test
	@set -e; for directory in $(JS_PACKAGES); do (cd "$$directory" && $(NODE) --test --test-concurrency=1 'test/*.test.mjs'); done

help:
	@printf '%s\n' \
	  'make build         Build host commands and container images' \
	  'make install       Build and install under PREFIX (default: $$HOME/.local)' \
	  'make install-host  Install only the asys observation command (no Docker needed)' \
	  'make test-deps     Install Node and TUI dependencies for development tests' \
	  'make test          Run Go, Python and Node tests (after test-deps)' \
	  'PREFIX and DESTDIR apply to all host tools; dcomp is installed separately.'
