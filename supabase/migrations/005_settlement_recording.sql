-- Settlement recording: track real-world payments to reduce balances
-- This closes the loop: suggest → pay → record → balances update

-- ---------------------------------------------------------------------------
-- Settlement payments table
-- ---------------------------------------------------------------------------
create table if not exists public.settlement_payments (
  id uuid primary key default gen_random_uuid(),
  group_id uuid not null references public.groups (id) on delete cascade,
  from_user_id uuid not null references auth.users (id) on delete cascade,
  to_user_id uuid not null references auth.users (id) on delete cascade,
  amount numeric(14, 2) not null check (amount > 0),
  currency text not null default 'INR',
  payment_date date not null default current_date,
  note text not null default '',
  recorded_by uuid not null references auth.users (id) default auth.uid(),
  created_at timestamptz not null default now(),
  constraint settlement_no_self_payment check (from_user_id <> to_user_id)
);

create index if not exists idx_settlements_group on public.settlement_payments (group_id, payment_date desc);
create index if not exists idx_settlements_from on public.settlement_payments (from_user_id);
create index if not exists idx_settlements_to on public.settlement_payments (to_user_id);

-- ---------------------------------------------------------------------------
-- RPC: record settlement payment
-- ---------------------------------------------------------------------------
create or replace function public.fn_record_settlement(
  p_group_id uuid,
  p_from_user_id uuid,
  p_to_user_id uuid,
  p_amount numeric,
  p_payment_date date default current_date,
  p_note text default ''
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_settlement_id uuid;
begin
  -- Verify caller is group member
  if not exists (
    select 1 from public.group_members m
    where m.group_id = p_group_id and m.user_id = auth.uid()
  ) then
    raise exception 'not a member of this group';
  end if;

  -- Verify both parties are group members
  if not exists (
    select 1 from public.group_members m
    where m.group_id = p_group_id and m.user_id = p_from_user_id
  ) then
    raise exception 'from_user is not a group member';
  end if;

  if not exists (
    select 1 from public.group_members m
    where m.group_id = p_group_id and m.user_id = p_to_user_id
  ) then
    raise exception 'to_user is not a group member';
  end if;

  if p_from_user_id = p_to_user_id then
    raise exception 'cannot settle payment to yourself';
  end if;

  if p_amount <= 0 then
    raise exception 'amount must be positive';
  end if;

  insert into public.settlement_payments (
    group_id,
    from_user_id,
    to_user_id,
    amount,
    payment_date,
    note,
    recorded_by
  )
  values (
    p_group_id,
    p_from_user_id,
    p_to_user_id,
    p_amount,
    coalesce(p_payment_date, current_date),
    coalesce(p_note, ''),
    auth.uid()
  )
  returning id into v_settlement_id;

  return v_settlement_id;
end;
$$;

grant execute on function public.fn_record_settlement(uuid, uuid, uuid, numeric, date, text) to authenticated;

-- ---------------------------------------------------------------------------
-- Enhanced balance calculation: includes settlements
-- ---------------------------------------------------------------------------
create or replace function public.fn_group_balances_with_settlements(p_group_id uuid)
returns table(user_id uuid, net_balance numeric)
language plpgsql
security definer
stable
as $$
begin
  return query
  with expense_balances as (
    -- Original logic: payer gets +amount, everyone gets -share
    select
      ts.member_id as user_id,
      -sum(ts.share_amount) as balance
    from public.transactions t
    join public.transaction_splits ts on ts.transaction_id = t.id
    where t.group_id = p_group_id
      and t.status = 'approved'
    group by ts.member_id

    union all

    select
      t.payer_id as user_id,
      sum(t.amount) as balance
    from public.transactions t
    where t.group_id = p_group_id
      and t.status = 'approved'
    group by t.payer_id
  ),
  settlement_adjustments as (
    -- Settlements: from_user pays (negative), to_user receives (positive)
    select from_user_id as user_id, -sum(amount) as balance
    from public.settlement_payments
    where group_id = p_group_id
    group by from_user_id

    union all

    select to_user_id as user_id, sum(amount) as balance
    from public.settlement_payments
    where group_id = p_group_id
    group by to_user_id
  ),
  combined as (
    select user_id, balance from expense_balances
    union all
    select user_id, balance from settlement_adjustments
  )
  select
    c.user_id,
    sum(c.balance) as net_balance
  from combined c
  group by c.user_id
  having abs(sum(c.balance)) > 0.001  -- filter near-zero balances
  order by sum(c.balance) desc;
end;
$$;

grant execute on function public.fn_group_balances_with_settlements(uuid) to authenticated;

-- ---------------------------------------------------------------------------
-- RLS for settlement_payments
-- ---------------------------------------------------------------------------
alter table public.settlement_payments enable row level security;

drop policy if exists settlements_select on public.settlement_payments;
create policy settlements_select on public.settlement_payments for select using (
  exists (
    select 1 from public.group_members gm
    where gm.group_id = settlement_payments.group_id
      and gm.user_id = auth.uid()
  )
);

drop policy if exists settlements_insert_none on public.settlement_payments;
create policy settlements_insert_none on public.settlement_payments for insert with check (false);

drop policy if exists settlements_update_none on public.settlement_payments;
create policy settlements_update_none on public.settlement_payments for update using (false);

drop policy if exists settlements_delete_none on public.settlement_payments;
create policy settlements_delete_none on public.settlement_payments for delete using (false);

-- All inserts go through fn_record_settlement RPC (security definer)
