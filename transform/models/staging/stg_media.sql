-- Silver: cleaned catalog. One row per title, HTML stripped from synopsis,
-- genre list parsed, latest ingestion wins.
with raw as (
    select
        *,
        row_number() over (partition by media_id order by _ingested_at desc) as rn
    from read_parquet('{{ var("bronze_root") }}/anilist_media/*/*.parquet')
)
select
    media_id,
    coalesce(title_english, title_romaji)                       as title,
    title_romaji,
    from_json(genres, '["VARCHAR"]')                            as genres,
    regexp_replace(coalesce(description_html, ''), '<[^>]+>', '', 'g') as synopsis,
    average_score,
    popularity,
    episodes,
    season,
    season_year,
    format
from raw
where rn = 1
