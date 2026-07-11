-- Gold: user dimension. Grain: one row per user.
select
    user_id            as user_key,
    signup_date,
    primary_device,
    region,
    favorite_genre,
    date_diff('day', signup_date, current_date) as tenure_days
from {{ ref('stg_users') }}
