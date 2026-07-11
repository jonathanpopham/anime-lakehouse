.PHONY: setup ingest simulate warehouse enrich eval test pipeline dagster

setup:
	uv venv --python 3.12 && uv pip install -e ".[dev,orchestration]"

ingest:
	python -m anime_lakehouse.ingest.anilist --pages 4

simulate:
	python -m anime_lakehouse.ingest.simulate_playback

warehouse:
	cd transform && dbt build --profiles-dir .

warehouse-enriched:
	cd transform && dbt build --profiles-dir . --vars '{enriched: true}'

enrich:
	python -m anime_lakehouse.llm.enrich

eval:
	python evals/run_eval.py

test:
	pytest -q

pipeline: ingest simulate warehouse

dagster:
	dagster dev -f orchestration/definitions.py
