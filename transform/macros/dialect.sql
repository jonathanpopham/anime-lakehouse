{#
  DuckDB and Spark SQL disagree on a handful of function signatures the models
  need. Each macro below is the one place that difference lives, so the models
  stay readable and a third engine would only have to extend these.
#}

{# DuckDB spells a JSON string array '["VARCHAR"]'; Spark spells it 'array<string>'. #}
{% macro parse_string_array(col) %}
  {%- if target.type == 'duckdb' -%}
    from_json({{ col }}, '["VARCHAR"]')
  {%- else -%}
    from_json({{ col }}, 'array<string>')
  {%- endif -%}
{% endmacro %}

{# Both engines are 1-indexed here, but Spark needs element_at rather than []. #}
{% macro first_element(col) %}
  {%- if target.type == 'duckdb' -%}
    {{ col }}[1]
  {%- else -%}
    element_at({{ col }}, 1)
  {%- endif -%}
{% endmacro %}

{# DuckDB: date_diff(part, start, end). Spark: datediff(end, start), days only. #}
{% macro days_between(start_date, end_date) %}
  {%- if target.type == 'duckdb' -%}
    date_diff('day', {{ start_date }}, {{ end_date }})
  {%- else -%}
    datediff({{ end_date }}, {{ start_date }})
  {%- endif -%}
{% endmacro %}

{# DuckDB needs a 'g' flag for global replace; Spark's regexp_replace is global
   already and reads a 4th argument as an integer start position. #}
{% macro strip_html(col) %}
  {%- if target.type == 'duckdb' -%}
    regexp_replace(coalesce({{ col }}, ''), '<[^>]+>', '', 'g')
  {%- else -%}
    regexp_replace(coalesce({{ col }}, ''), '<[^>]+>', '')
  {%- endif -%}
{% endmacro %}
