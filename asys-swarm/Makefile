PREFIX ?= $(HOME)/.local
.PHONY: host build install-host install test integration-test
host:
	install -D -m 0755 tools/asys-swarm bin/asys-swarm
build: host
	$(MAKE) -C ../asys-workers build
install-host: host
	$(MAKE) -C .. install-host
	install -D -m 0755 bin/asys-swarm "$(DESTDIR)$(PREFIX)/bin/asys-swarm"
	install -d "$(DESTDIR)$(PREFIX)/share/asys-swarm/python/asys_swarm"
	install -m 0644 ../asys-workers/asys_swarm/*.py "$(DESTDIR)$(PREFIX)/share/asys-swarm/python/asys_swarm/"
	install -m 0644 ../LICENSE ../asys-runtime/LICENSE.multiagent "$(DESTDIR)$(PREFIX)/share/asys-swarm/"
	install -m 0644 README.md AUTHORING.md WORLD.md "$(DESTDIR)$(PREFIX)/share/asys-swarm/"
	@find examples -type f ! -name '*.pyc' ! -path '*/__pycache__/*' -exec sh -ec '\
		destination=$$1; shift; \
		for source do install -D -m 0644 "$$source" "$$destination/$$source"; done' \
		_ "$(DESTDIR)$(PREFIX)/share/asys-swarm" {} +
install: build install-host
test:
	PYTHONPATH=../python:../asys-runtime:../asys-workers python3 -m unittest discover -s test
integration-test:
	tools/integration-test
