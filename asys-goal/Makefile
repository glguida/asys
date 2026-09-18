PREFIX ?= $(HOME)/.local
.PHONY: host build install-host install test
host:
	install -D -m 0755 tools/asys-goal bin/asys-goal
build: host
	$(MAKE) -C ../asys-workers build
	$(MAKE) -C ../asys-human-interface build
install-host: host
	$(MAKE) -C .. install-host
	install -D -m 0755 bin/asys-goal "$(DESTDIR)$(PREFIX)/bin/asys-goal"
install: build install-host
	$(MAKE) -C ../asys-human-interface install-host
test:
	python3 -m unittest discover -s test
