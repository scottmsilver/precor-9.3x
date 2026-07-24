.PHONY: test generate erc drc sim enclosure fab stock check clean-check

test:
	PYTHONPATH=../.. python3 -m pytest -q tests

generate:
	python3 tools/gen_footprints.py
	python3 tools/gen_sch.py
	/usr/bin/python3 tools/gen_pcb.py
	/usr/bin/python3 tools/gen_docs.py

erc:
	python3 tools/repro_check.py --write-report erc

drc:
	python3 tools/repro_check.py --write-report drc

sim:
	python3 sim/run_simulations.py

enclosure:
	python3 enclosure/validate_enclosure.py

fab:
	python3 tools/export_fab.py

stock:
	python3 tools/check_jlc_stock.py

check:
	PYTHONPATH=../.. python3 -m pytest -q tests
	python3 tools/repro_check.py
	python3 sim/run_simulations.py
	python3 enclosure/validate_enclosure.py
	python3 tools/export_fab.py --audit-only
	python3 tools/check_jlc_stock.py

clean-check:
	python3 tools/repro_check.py
