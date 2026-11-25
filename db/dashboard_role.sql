-- Run this ONCE in the Supabase SQL Editor before deploying the public dashboard.
--
-- It creates a database user that can only READ the three tables the dashboard
-- needs. The public app logs in as this user, so even if its password leaked,
-- nobody could change or delete anything.
--
-- 1. Replace CHANGE_ME_long_password below with a long password
--    (letters and numbers only, so it works inside a connection string).
-- 2. Run the whole script.

create role dashboard_reader with login password 'CHANGE_ME_long_password';

grant usage on schema public to dashboard_reader;
grant usage on schema extensions to dashboard_reader;
grant select on companies, news_items, ingest_runs, story_clusters to dashboard_reader;

-- Row level security is on for these tables with no policies, which blocks
-- everyone except the table owner. These policies let dashboard_reader read
-- (and only read) every row.
drop policy if exists dashboard_read on companies;
drop policy if exists dashboard_read on news_items;
drop policy if exists dashboard_read on ingest_runs;
create policy dashboard_read on companies   for select to dashboard_reader using (true);
create policy dashboard_read on news_items  for select to dashboard_reader using (true);
create policy dashboard_read on ingest_runs for select to dashboard_reader using (true);
