PREFIX ?= $(HOME)/.local
.PHONY: host build install-host install test
host:
	install -D -m 0755 tools/asys-senate bin/asys-senate
build: host
	$(MAKE) -C ../asys-workers build
install-host: host
	$(MAKE) -C .. install-host
	install -D -m 0755 bin/asys-senate "$(DESTDIR)$(PREFIX)/bin/asys-senate"
install: build install-host
test:
	python3 -m unittest discover -s test
