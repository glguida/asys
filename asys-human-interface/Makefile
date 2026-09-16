PREFIX ?= $(HOME)/.local
HOST_DEPS ?= .host-deps
.PHONY: host host-deps build install install-host test

host: host-deps
	install -D -m 0755 tools/asys-human-prompt bin/asys-human-prompt

host-deps: $(HOST_DEPS)/.installed

$(HOST_DEPS)/.installed: requirements-host.txt
	python3 -m pip install --disable-pip-version-check --no-compile --no-deps --upgrade --target "$(HOST_DEPS)" -r requirements-host.txt
	touch "$@"

build: host
	docker build -f ../asys-runtime/Dockerfile -t asys-runtime:dev ..
	docker build -f Dockerfile -t asys-human-interface:dev ..

install-host: host
	$(MAKE) -C .. install-host
	install -D -m 0755 bin/asys-human-prompt "$(DESTDIR)$(PREFIX)/bin/asys-human-prompt"
	install -d "$(DESTDIR)$(PREFIX)/share/asys-human/python/asys_human"
	install -m 0644 python/asys_human/*.py "$(DESTDIR)$(PREFIX)/share/asys-human/python/asys_human/"
	install -d "$(DESTDIR)$(PREFIX)/share/asys-human/python/asys_runtime"
	install -m 0644 ../asys-runtime/asys_runtime/*.py "$(DESTDIR)$(PREFIX)/share/asys-human/python/asys_runtime/"
	install -m 0644 ../LICENSE ../asys-runtime/LICENSE.multiagent "$(DESTDIR)$(PREFIX)/share/asys-human/"
	install -d "$(DESTDIR)$(PREFIX)/share/asys-human/vendor"
	cp -a "$(HOST_DEPS)/." "$(DESTDIR)$(PREFIX)/share/asys-human/vendor/"

install: build install-host

test: host-deps
	npm test
