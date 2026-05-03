-- Settlement payment recording
-- Tracks real-world payments that reduce balances

-- ---------------------------------------------------------------------------
-- Settlement payments table
-- ---------------------------------------------------------------------------
create table if not exists public.settlement_payments (
  id uuid primary key default gen_random_uuid(),
  group_id uuid not null references public.groups(id) on delete cascade,
  from_user_id uuid not null references auth.users(id) on delete cascade,
  to_user_id uuid not null references auth.users(id) on delete cascade,
  amount numeric(14, 2) not null check (amount > 0),
  currency text not null default 'USD',
  payment_date date not null default current_date,
  payment_method text, -- e.g., 'cash', 'venmo', 'bank_transfer'
  note text not null default '',
  recorded_by uuid not null references auth.users(id) default auth.uid(),
  created_at timestamptz not null default now(),
  constraint settlement_no_self_payment check (from_user_id <> to_user_id)
);

create index if not exists idx_settlements_group on public.settlement_payments(group_id, payment_date desc);
create index if not exists idx_settlements_from on public.settlement_payments(from_user_id);
create index if not exists idx_settlements_to on public.settlement_payments(to_user_id);

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

drop policy if exists settlements_insert on public.settlement_payments;
create policy settlements_insert on public.settlement_payments for insert with check (
  exists (
    select 1 from public.group_members gm
    where gm.group_id = settlement_payments.group_id
      and gm.user_id = auth.uid()
  )
  and recorded_by = auth.uid()
);

-- ---------------------------------------------------------------------------
-- RPC: Record settlement payment
-- ---------------------------------------------------------------------------
create or replace function public.fn_record_settlement(
  p_group_id uuid,
  p_from_user_id uuid,
  p_to_user_id uuid,
  p_amount numeric,
  p_payment_date date default current_date,
  p_payment_method text default null,
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
    select 1 from public.group_members gm
    where gm.group_id = p_group_id and gm.user_id = auth.uid()
  ) then
    raise exception 'not a member of this group';
  end if;

  -- Verify both parties are group members
  if not exists (
    select 1 from public.group_members gm
    where gm.group_id = p_group_id and gm.user_id = p_from_user_id
  ) then
    raise exception 'from_user is not a group member';
  end if;

  if not exists (
    select 1 from public.group_members gm
    where gm.group_id = p_group_id and gm.user_id = p_to_user_id
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
    payment_method,
    note,
    recorded_by
  )
  values (
    p_group_id,
    p_from_user_id,
    p_to_user_id,
    p_amount,
    coalesce(p_payment_date, current_date),
    p_payment_method,
    coalesce(p_note, ''),
    auth.uid()
  )
  returning id into v_settlement_id;

  return v_settlement_id;
end;
$$;

grant execute on function public.fn_record_settlement(
  uuid, uuid, uuid, numeric, date, text, text
) to authenticated;

-- ---------------------------------------------------------------------------
-- Enhanced balance calculation (includes settlements)
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
    -- Original expense-based balances
    select
      coalesce(payers.user_id, splitters.user_id) as user_id,
      coalesce(payers.paid, 0) - coalesce(splitters.owed, 0) as balance
    from (
      select
        t.payer_id as user_id,
        sum(t.amount) as paid
      from public.transactions t
      where t.group_id = p_group_id
        and t.status = 'approved'
      group by t.payer_id
    ) payers
    full outer join (
      select
        ts.member_id as user_id,
        sum(ts.share_amount) as owed
      from public.transaction_splits ts
      join public.transactions t on t.id = ts.transaction_id
      where t.group_id = p_group_id
        and t.status = 'approved'
      group by ts.member_id
    ) splitters on payers.user_id = splitters.user_id
  ),
  settlement_adjustments as (
    -- Settlement payments reduce balances
    select
      sp.from_user_id as user_id,
      -sum(sp.amount) as adjustment
    from public.settlement_payments sp
    where sp.group_id = p_group_id
    group by sp.from_user_id
    
    union all
    
    select
      sp.to_user_id as user_id,
      sum(sp.amount) as adjustment
    from public.settlement_payments sp
    where sp.group_id = p_group_id
    group by sp.to_user_id
  )
  select
    coalesce(eb.user_id, sa.user_id) as user_id,
    coalesce(eb.balance, 0) + coalesce(sum(sa.adjustment), 0) as net_balance
  from expense_balances eb
  full outer join settlement_adjustments sa on eb.user_id = sa.user_id
  group by eb.user_id, eb.balance, sa.user_id
  order by net_balance desc;
end;
$$;

grant execute on function public.fn_group_balances_with_settlements(uuid) to authenticated;
