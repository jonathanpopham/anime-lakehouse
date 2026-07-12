select
    user_id,
    cast(signup_date as date) as signup_date,
    primary_device,
    region,
    favorite_genre
from {{ bronze('users', 'users/*.parquet') }}
