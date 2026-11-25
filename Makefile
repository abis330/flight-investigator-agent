.PHONY: install test data incidents demo eval eval-claude mcp docker
install:
	pip install -e ".[dev,anthropic]"
test:
	pytest -q
data:
	flightlake simulate --n 300 --out data/raw --workers 4 && flightlake ingest data/raw --lake data/lake --workers 4
incidents:
	investigator build-incidents --lake data/lake --out data/incidents.json
demo:
	investigator investigate FLT-00010 --lake data/lake --incidents data/incidents.json
eval:
	investigator evaluate --provider scripted
eval-claude:
	investigator evaluate --provider anthropic --limit 40
mcp:
	python -m flightinvestigator.mcp_server
docker:
	docker compose up --build
