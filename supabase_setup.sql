-- FB Deal Hunter - Supabase setup
-- Safe to run on a brand-new project AND on an existing one (it only adds what is missing).
-- Supabase dashboard -> SQL Editor -> paste this file -> Run.

create table if not exists public.listings (
  id text primary key,
  title text,
  price numeric,
  url text,
  image_url text,
  status text default 'NEW',
  deal_tier text,
  defect_summary text,
  auto_message_text text,
  outreach_log text,
  created_at timestamptz default now(),
  last_checked timestamptz
);

alter table public.listings
  add column if not exists normalized_model text,
  add column if not exists live_comp_price numeric default 0,
  add column if not exists local_comp_price numeric,
  add column if not exists market_value numeric,
  add column if not exists comp_source text,
  add column if not exists price_status text,
  add column if not exists cheaper_deal_id text,
  add column if not exists cheaper_deal_url text,
  add column if not exists cheaper_deal_price numeric,
  add column if not exists estimated_repair_cost numeric,
  add column if not exists projected_profit numeric,
  add column if not exists condition_grade text,
  add column if not exists scam_risk text,
  add column if not exists description text,
  add column if not exists location text,
  add column if not exists fingerprint text,
  add column if not exists filter_reason text,
  add column if not exists previous_price numeric,
  add column if not exists found_by text,
  add column if not exists seller_id text,
  add column if not exists seller_name text,
  add column if not exists contacted_by text,
  add column if not exists contacted_at timestamptz,
  add column if not exists listed_at timestamptz;

create index if not exists listings_status_created_idx on public.listings (status, created_at desc);
create index if not exists listings_model_created_idx on public.listings (normalized_model, created_at desc);
create index if not exists listings_fingerprint_idx on public.listings (fingerprint);
create index if not exists listings_seller_idx on public.listings (seller_id);

create table if not exists public.bot_settings (
  id integer primary key,
  target_city text,
  keywords text,
  min_price integer,
  max_price integer,
  is_active boolean default false,
  auto_message_enabled boolean default false
);

-- Default settings row (edit later from the dashboard's Scanner Controls tab)
insert into public.bot_settings (id, target_city, keywords, min_price, max_price, is_active, auto_message_enabled)
values (1, 'toronto', 'iphone, galaxy s, pixel', 100, 2500, false, false)
on conflict (id) do nothing;

notify pgrst, 'reload schema';
