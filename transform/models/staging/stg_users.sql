select
    user_id,
    cast(signup_date as date) as signup_date,
    primary_device,
    region,
    favorite_genre
from read_parquet('{{ var("bronze_root") }}/users/*.parquet')
