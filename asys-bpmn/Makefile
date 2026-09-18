PREFIX ?= $(HOME)/.local
BPMN_ELEMENTS ?= latest
MODDLE_CONTEXT_SERIALIZER ?= latest
.PHONY: test test-upstream-drift build install
test:
	npm test
test-upstream-drift:
	node tools/test-upstream-drift.mjs "$(BPMN_ELEMENTS)" "$(MODDLE_CONTEXT_SERIALIZER)"
build:
	install -D -m 0755 tools/asys-bpmn bin/asys-bpmn
	$(MAKE) -C ../asys-workers build
	docker build -t asys-hello-dummy:dev examples/hello/env/dummy
	docker build -f Dockerfile -t asys-bpmn:dev ..
install: build
	$(MAKE) -C .. install-host
	install -D -m 0755 bin/asys-bpmn "$(DESTDIR)$(PREFIX)/bin/asys-bpmn"
	install -d "$(DESTDIR)$(PREFIX)/share/asys-bpmn/python/asys_bpmn"
	install -m 0644 python/asys_bpmn/*.py "$(DESTDIR)$(PREFIX)/share/asys-bpmn/python/asys_bpmn/"
	install -m 0644 ../LICENSE ../asys-runtime/LICENSE.multiagent "$(DESTDIR)$(PREFIX)/share/asys-bpmn/"
