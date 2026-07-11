-- Gold: title dimension. Grain: one row per title.
-- mood_tags is populated by the LLM enrichment pipeline. Build with
-- --vars '{enriched: true}' once data/enriched/title_moods.parquet exists;
-- the default keeps the warehouse buildable before enrichment has run.
with enrichment as (
    {% if var('enriched', false) %}
    select media_id, mood_tags, enrichment_model, enriched_at
    from read_parquet('{{ var("bronze_root") }}/../enriched/title_moods.parquet')
    {% else %}
    select null::bigint as media_id, null::varchar[] as mood_tags,
           null::varchar as enrichment_model, null::timestamp as enriched_at
    where false
    {% endif %}
)
select
    m.media_id                      as title_key,
    m.title,
    m.title_romaji,
    m.genres,
    m.genres[1]                     as primary_genre,
    m.synopsis,
    m.average_score,
    m.popularity,
    m.episodes,
    m.season,
    m.season_year,
    m.format,
    e.mood_tags,
    e.enrichment_model,
    e.enriched_at
from {{ ref('stg_media') }} m
left join enrichment e using (media_id)
