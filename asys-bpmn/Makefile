PREFIX ?= $(HOME)/.local
.PHONY: test build install
test:
	npm test
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
