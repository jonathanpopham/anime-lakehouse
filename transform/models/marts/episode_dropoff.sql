-- Gold mart: episode-2 retention per title — the lab's headline question.
-- ep2_retention = of users who started episode 1, what share started episode 2.
with ep_starts as (
    select
        title_key,
        episode,
        count(distinct user_key) as viewers
    from {{ ref('fact_playback') }}
    where episode <= 2
    group by 1, 2
),
pivoted as (
    select
        title_key,
        max(case when episode = 1 then viewers end) as ep1_viewers,
        max(case when episode = 2 then viewers end) as ep2_viewers
    from ep_starts
    group by 1
)
select
    t.title,
    t.primary_genre,
    t.mood_tags,
    p.ep1_viewers,
    coalesce(p.ep2_viewers, 0) as ep2_viewers,
    round(coalesce(p.ep2_viewers, 0) * 1.0 / nullif(p.ep1_viewers, 0), 4) as ep2_retention
from pivoted p
join {{ ref('dim_title') }} t using (title_key)
where p.ep1_viewers >= 20  -- suppress noise from barely-watched titles
order by ep2_retention desc
