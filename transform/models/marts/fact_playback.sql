-- Gold: playback fact. Grain: one row per (user, title, episode) viewing.
select
    p.event_id,
    p.user_id       as user_key,
    p.media_id      as title_key,
    p.episode,
    p.started_at,
    cast(p.started_at as date) as watch_date,
    p.watch_pct,
    p.completed,
    p.rebuffer_count,
    p.startup_time_ms,
    p.device,
    p.region
from {{ ref('stg_playback') }} p
