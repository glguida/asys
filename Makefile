.DEFAULT_GOAL := build

PREFIX ?= $(HOME)/.local
DESTDIR ?=
PYTHON ?= python3
NODE ?= node
NPM ?= npm
export PREFIX DESTDIR

JS_PACKAGES := $(patsubst %/package.json,%,$(wildcard asys-inference/components/protocol/*/package.json asys-inference/components/*/package.json)) asys-workers asys-bpmn asys-human-interface

.PHONY: all host install-host install-skills build install test test-deps help

all: build

host:
	install -D -m 0755 tools/asys bin/asys

install-host: host install-skills
	install -D -m 0755 bin/asys "$(DESTDIR)$(PREFIX)/bin/asys"
	install -d "$(DESTDIR)$(PREFIX)/share/asys/python/asys"
	install -m 0644 python/asys/*.py "$(DESTDIR)$(PREFIX)/share/asys/python/asys/"
	@set -e; for source in python/asys/system_agents/*.py python/asys/system_agents/*/prompt.md; do \
		install -D -m 0644 "$$source" "$(DESTDIR)$(PREFIX)/share/asys/$$source"; \
	done
	install -d "$(DESTDIR)$(PREFIX)/share/asys/python/asys_runtime"
	install -m 0644 asys-runtime/asys_runtime/*.py "$(DESTDIR)$(PREFIX)/share/asys/python/asys_runtime/"
	install -m 0644 LICENSE "$(DESTDIR)$(PREFIX)/share/asys/"
	install -m 0644 asys-runtime/LICENSE.multiagent "$(DESTDIR)$(PREFIX)/share/asys/"

install-skills:
	@find skills/asys -type f -exec sh -ec '\
		destination=$$1; shift; \
		for source do install -D -m 0644 "$$source" "$$destination/$$source"; done' \
		_ "$(DESTDIR)$(PREFIX)/share/asys" {} +

build: host
	$(MAKE) -C asys-inference build
	$(MAKE) -C asys-bpmn build
	$(MAKE) -C asys-swarm build
	$(MAKE) -C asys-oneshot host
	$(MAKE) -C asys-goal host
	$(MAKE) -C asys-senate host
	$(MAKE) -C asys-human-interface build

install:
	$(MAKE) -C asys-inference install
	$(MAKE) -C asys-bpmn install
	$(MAKE) -C asys-swarm install
	$(MAKE) -C asys-oneshot install-host
	$(MAKE) -C asys-goal install-host
	$(MAKE) -C asys-senate install-host
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
	$(MAKE) -C asys-goal test
	$(MAKE) -C asys-senate test
	$(MAKE) -C asys-swarm test
	@set -e; for directory in $(JS_PACKAGES); do (cd "$$directory" && $(NODE) --test --test-concurrency=1 'test/*.test.mjs'); done

help:
	@printf '%s\n' \
	  'make build         Build host commands and container images' \
	  'make install       Build and install under PREFIX (default: $$HOME/.local)' \
	  'make install-host  Install the core command, system agents, and portable skill (no Docker needed)' \
	  'make test-deps     Install Node and TUI dependencies for development tests' \
	  'make test          Run Go, Python and Node tests (after test-deps)' \
	  'PREFIX and DESTDIR apply to all host tools; dcomp is installed separately.'
