PREFIX ?= $(HOME)/.local
.PHONY: host build install-host install test
host:
	install -D -m 0755 tools/asys-oneshot bin/asys-oneshot
build: host
	$(MAKE) -C ../asys-workers build
install-host: host
	$(MAKE) -C .. install-host
	install -D -m 0755 bin/asys-oneshot "$(DESTDIR)$(PREFIX)/bin/asys-oneshot"
install: build install-host
test:
	python3 -m unittest discover -s test
