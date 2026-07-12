-- Silver: cleaned catalog. One row per title, HTML stripped from synopsis,
-- genre list parsed, latest ingestion wins.
with raw as (
    select
        *,
        row_number() over (partition by media_id order by _ingested_at desc) as rn
    from {{ bronze('anilist_media', 'anilist_media/*/*.parquet') }}
)
select
    media_id,
    coalesce(title_english, title_romaji)                       as title,
    title_romaji,
    {{ parse_string_array('genres') }} as genres,
    {{ strip_html('description_html') }} as synopsis,
    average_score,
    popularity,
    episodes,
    season,
    season_year,
    format
from raw
where rn = 1
