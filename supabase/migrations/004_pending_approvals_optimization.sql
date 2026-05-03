-- Performance optimization: materialized view for pending approvals
-- Eliminates N+1 queries in _with_pending_hint()

-- ---------------------------------------------------------------------------
-- Materialized view: pre-computed actionable pending approvals per user
-- ---------------------------------------------------------------------------
create materialized view if not exists public.mv_user_pending_approvals as
select
  gm.user_id,
  t.id as transaction_id,
  t.group_id,
  t.expense_date,
  t.amount,
  t.category,
  t.submitted_by,
  t.status
from public.transactions t
join public.group_members gm on gm.group_id = t.group_id
where
  t.status = 'pending'
  and t.group_id is not null
  and gm.user_id <> t.submitted_by  -- exclude submitter
  and not exists (
    select 1 from public.transaction_approvals ta
    where ta.transaction_id = t.id
      and ta.voter_id = gm.user_id
      and ta.decision = 'approve'
  );

create unique index if not exists idx_mv_pending_user_tx
  on public.mv_user_pending_approvals (user_id, transaction_id);

create index if not exists idx_mv_pending_user
  on public.mv_user_pending_approvals (user_id);

-- ---------------------------------------------------------------------------
-- Refresh function (called by triggers)
-- ---------------------------------------------------------------------------
create or replace function public.refresh_pending_approvals_mv()
returns void
language plpgsql
security definer
as $$
begin
  refresh materialized view concurrently public.mv_user_pending_approvals;
end;
$$;

-- ---------------------------------------------------------------------------
-- Triggers: auto-refresh on relevant changes
-- ---------------------------------------------------------------------------
create or replace function public.trigger_refresh_pending_mv()
returns trigger
language plpgsql
as $$
begin
  perform public.refresh_pending_approvals_mv();
  return null;
end;
$$;

drop trigger if exists trg_transactions_refresh_pending on public.transactions;
create trigger trg_transactions_refresh_pending
  after insert or update of status on public.transactions
  for each statement
  execute function trigger_refresh_pending_mv();

drop trigger if exists trg_approvals_refresh_pending on public.transaction_approvals;
create trigger trg_approvals_refresh_pending
  after insert or update on public.transaction_approvals
  for each statement
  execute function trigger_refresh_pending_mv();

-- ---------------------------------------------------------------------------
-- Fast lookup function (replaces _fetch_actionable_pending)
-- ---------------------------------------------------------------------------
create or replace function public.fn_get_pending_count(p_user_id uuid)
returns table(count bigint, sample jsonb)
language plpgsql
security definer
stable
as $$
begin
  return query
  select
    count(*) as count,
    jsonb_agg(
      jsonb_build_object(
        'id', transaction_id,
        'group_id', group_id,
        'expense_date', expense_date,
        'amount', amount,
        'category', category,
        'submitted_by', submitted_by
      )
      order by expense_date desc
    ) filter (where row_number() over (order by expense_date desc) <= 20) as sample
  from public.mv_user_pending_approvals
  where user_id = p_user_id;
end;
$$;

grant execute on function public.fn_get_pending_count(uuid) to authenticated;

-- ---------------------------------------------------------------------------
-- RLS for materialized view
-- ---------------------------------------------------------------------------
alter materialized view public.mv_user_pending_approvals owner to postgres;

-- Note: Materialized views don't support RLS directly, but the function
-- fn_get_pending_count is SECURITY DEFINER and filters by p_user_id,
-- which should match auth.uid() when called from application code.

-- ---------------------------------------------------------------------------
-- Initial refresh
-- ---------------------------------------------------------------------------
refresh materialized view public.mv_user_pending_approvals;
