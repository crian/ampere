# Build and install helper for Ampere.
#
#   make help        list the targets
#
# The GNOME extension lives in extension/, the companion service in daemon/.

SHELL := /bin/bash

EXT_DIR  := extension
UUID     := $(shell python3 -c 'import json; print(json.load(open("$(EXT_DIR)/metadata.json"))["uuid"])')
BUILD    := build
PACKAGE  := $(BUILD)/$(UUID).shell-extension.zip
DEV_LINK := $(HOME)/.local/share/gnome-shell/extensions/$(UUID)

# Runtime modules that `gnome-extensions pack` does not pick up on its own.
EXTRA_SOURCES := utils.js hidbattery.js indicator.js

.PHONY: help schemas pack check-pack install uninstall dev undev lint \
        test test-py test-js daemon daemon-install daemon-uninstall clean

help:
	@echo "Extension:"
	@echo "  make schemas          compile the GSettings schema"
	@echo "  make pack             build $(PACKAGE)"
	@echo "  make check-pack       verify the contents of $(PACKAGE)"
	@echo "  make install          pack and install into ~/.local/share/gnome-shell/extensions"
	@echo "  make uninstall        remove the installed extension"
	@echo "  make dev              symlink $(EXT_DIR)/ into the extensions directory (for development)"
	@echo "  make undev            remove that symlink"
	@echo "  make lint             ESLint on the extension and JS tests (needs npx)"
	@echo "  make test-js          run the extension JS unit tests (needs gjs)"
	@echo "Companion service:"
	@echo "  make test-py          run the daemon tests"
	@echo "  make test             run both Python and JS tests"
	@echo "  make daemon           build the single file ./ampere-hid"
	@echo "  make daemon-install   build and install the service (add UDEV=1 for the udev rule)"
	@echo "  make daemon-uninstall remove the service (add UDEV=1 to remove the udev rule)"
	@echo "Other:"
	@echo "  make clean            remove build output"
	@echo
	@echo "Log out and back in after installing; Wayland cannot reload the shell in place."

schemas:
	glib-compile-schemas --strict "$(EXT_DIR)/schemas/"

pack: schemas
	mkdir -p "$(BUILD)"
	gnome-extensions pack --force \
		$(addprefix --extra-source=,$(EXTRA_SOURCES)) \
		--extra-source="$(CURDIR)/LICENSE" \
		--out-dir="$(BUILD)" \
		"$(EXT_DIR)"
	@echo "Built $(PACKAGE)"

check-pack:
	@test -f "$(PACKAGE)" || { echo "$(PACKAGE) not found; run 'make pack' first" >&2; exit 1; }
	@unzip -l "$(PACKAGE)"
	@if unzip -l "$(PACKAGE)" \
		| grep -E 'daemon/|tests/|README|CONTRIBUTING|screenshots/|eslint|Makefile|\.png'; then \
		echo "package contains files that do not belong there" >&2; \
		exit 1; \
	fi
	@for f in extension.js prefs.js $(EXTRA_SOURCES) metadata.json \
		stylesheet.css LICENSE \
		schemas/org.gnome.shell.extensions.ampere.gschema.xml; do \
		unzip -l "$(PACKAGE)" | grep -q " $$f$$" || { \
			echo "package is missing $$f" >&2; \
			exit 1; \
		}; \
	done

install: pack
	gnome-extensions install --force "$(PACKAGE)"
	@echo "Installed $(UUID). Log out and back in, then enable it."

uninstall:
	gnome-extensions uninstall "$(UUID)"

# Development: the shell loads the checkout directly, so the compiled schema
# has to exist inside extension/schemas/.
dev: schemas
	@if [ -e "$(DEV_LINK)" ] && [ ! -L "$(DEV_LINK)" ]; then \
		echo "$(DEV_LINK) already exists and is not a symlink; run 'make uninstall' first" >&2; \
		exit 1; \
	fi
	mkdir -p "$(dir $(DEV_LINK))"
	ln -sfn "$(CURDIR)/$(EXT_DIR)" "$(DEV_LINK)"
	@echo "Linked $(DEV_LINK) -> $(CURDIR)/$(EXT_DIR)"

undev:
	@if [ -L "$(DEV_LINK)" ]; then rm "$(DEV_LINK)"; echo "Removed $(DEV_LINK)"; \
	else echo "$(DEV_LINK) is not a symlink, left alone"; fi

lint:
	npx --yes eslint@10 "$(EXT_DIR)" tests

test: test-py test-js

test-py:
	python3 -m unittest discover -s daemon/tests -v

test-js:
	gjs -m tests/test_extension.js

daemon:
	cd daemon && python3 -m ampere_hid build ../ampere-hid

daemon-install:
	cd daemon && python3 -m ampere_hid install $(if $(UDEV),--udev,)

daemon-uninstall:
	cd daemon && python3 -m ampere_hid uninstall $(if $(UDEV),--udev,)

clean:
	rm -rf "$(BUILD)" ampere-hid ampere-hid.sha256 "$(EXT_DIR)/schemas/gschemas.compiled"
