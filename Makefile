.PHONY: up seed ground-truth benchmark test reproduce

MODEL_LABEL ?= qwen3.5-9b-100k

up:
	docker compose up --build -d

seed:
	docker compose exec queryx python -m queryx.tools.seed_demo

ground-truth:
	docker compose exec queryx python -m benchmark.generate_ground_truth

benchmark:
	docker compose exec queryx python -m benchmark.run \
		--base-url http://127.0.0.1:8000 \
		--cases /app/benchmark/cases.json \
		--output-dir /app/benchmark/results \
		--model-label $(MODEL_LABEL)

test:
	docker compose exec queryx pytest -q tests/test_benchmark.py tests/test_seed_demo.py

reproduce: up seed ground-truth benchmark
