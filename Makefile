.PHONY: build up down logs calls listeners status shell venv validate test test-stream

VENV_PYTHON := .venv/bin/python3

build:
	docker compose build

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

calls:
	docker compose logs --no-log-prefix | grep 'CALL_START\|CALL_END'

listeners:
	docker compose exec --user asterisk sip-sdr-service asterisk -rx "group show channels"

status:
	docker compose ps
	docker compose exec --user asterisk sip-sdr-service asterisk -rx "pjsip show registrations"
	docker compose exec --user asterisk sip-sdr-service asterisk -rx "moh show classes"
	docker compose exec --user asterisk sip-sdr-service asterisk -rx "group show channels"

shell:
	docker compose exec --user asterisk sip-sdr-service asterisk -rvvv

venv:
	test -d .venv || python3 -m venv .venv
	$(VENV_PYTHON) -m pip install --quiet --upgrade pip
	$(VENV_PYTHON) -m pip install --quiet numpy scipy

validate: venv
	ENV_FILE=.env.example docker compose --env-file .env.example config --quiet
	sh -n docker-entrypoint.sh
	sh -n scripts/resolve_entry_context.sh
	$(VENV_PYTHON) -m py_compile scripts/sdr_env.py scripts/sdr_demod.py scripts/sdr_stream.py scripts/sdr_tune.py scripts/sdr_backends/sdrplay.py scripts/sdr_backends/rtlsdr.py scripts/sdr_backends/plutosdr.py scripts/test_sdr.py scripts/healthcheck.py

test: validate
	$(VENV_PYTHON) -m unittest discover -s tests -v

test-stream:
	docker compose exec --user asterisk sip-sdr-service /usr/local/bin/test-sdr-receiver
