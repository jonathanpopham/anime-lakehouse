{#
  One dbt project, two engines. Locally the bronze layer is parquet on disk and
  DuckDB reads it with read_parquet(); on Databricks the same data is a Delta
  table in Unity Catalog. Spark SQL has no read_parquet() table function, so the
  staging models cannot name either form directly.

  bronze() returns the right relation for the active target, which keeps the
  dialect switch in exactly one place instead of forking every staging model.

  `path` is the local glob under bronze_root; `table` is the Unity Catalog table
  name. They differ because local ingestion partitions by ingest date.
#}
{% macro bronze(table, path) %}
  {%- if target.type == 'duckdb' -%}
    read_parquet('{{ var("bronze_root") }}/{{ path }}')
  {%- else -%}
    {{ target.catalog }}.bronze.{{ table }}
  {%- endif -%}
{% endmacro %}
