.PHONY: up wait preflight seed scan ground-truth benchmark test test-reproduction reproduce

MODEL_LABEL ?= qwen3.5-9b-100k
BASE_URL ?= http://localhost:8000
MYSQL_SOURCE_ID ?= mysql
MONGODB_SOURCE_ID ?= mongodb

up:
	docker compose up --build -d

wait:
	@echo "Waiting for QueryX API..."
	@for i in $$(seq 1 60); do \
		if curl -fsS "$(BASE_URL)/health" >/dev/null; then \
			echo "QueryX API is ready."; \
			exit 0; \
		fi; \
		sleep 2; \
	done; \
	echo "QueryX API did not become ready in time."; \
	exit 1

preflight:
	@bash scripts/reproduce.sh --preflight-only

seed:
	docker compose exec queryx python -m queryx.tools.seed_demo

scan:
	curl -fsS -X POST "$(BASE_URL)/sources/$(MYSQL_SOURCE_ID)/scan"
	curl -fsS -X POST "$(BASE_URL)/sources/$(MONGODB_SOURCE_ID)/scan"

ground-truth:
	docker compose exec queryx python -m benchmark.generate_ground_truth

benchmark:
	docker compose exec queryx python -m benchmark.run \
		--base-url http://127.0.0.1:8000 \
		--cases /app/benchmark/cases.json \
		--output-dir /app/benchmark/results \
		--model-label "$(MODEL_LABEL)"

test:
	docker compose exec queryx pytest -q

test-reproduction:
	docker compose exec queryx pytest -q \
		tests/test_benchmark.py \
		tests/test_seed_demo.py

reproduce:
	@MODEL_LABEL="$(MODEL_LABEL)" BASE_URL="$(BASE_URL)" \
		MYSQL_SOURCE_ID="$(MYSQL_SOURCE_ID)" \
		MONGODB_SOURCE_ID="$(MONGODB_SOURCE_ID)" \
		bash scripts/reproduce.sh
