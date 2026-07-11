-- Silver: validated playback events. Drops malformed rows, types timestamps.
select
    event_id,
    user_id,
    media_id,
    episode,
    cast(started_at as timestamp)  as started_at,
    watch_pct,
    completed,
    rebuffer_count,
    startup_time_ms,
    device,
    region
from read_parquet('{{ var("bronze_root") }}/playback_events/*.parquet')
where user_id is not null
  and media_id is not null
  and watch_pct between 0 and 1
