-- Collaborative finance: groups, unified transactions (personal + group), approvals, splits.
-- Run after 001_expenses.sql (optional legacy) or standalone if you migrate data manually.
--
-- Implements: group create, invites, redeem, add group expense (equal split, pending approval),
-- vote approve/reject (all_other_members by default).

-- ---------------------------------------------------------------------------
-- Types
-- ---------------------------------------------------------------------------
do $$ begin
  create type public.member_role as enum ('owner', 'admin', 'member');
exception when duplicate_object then null;
end $$;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------
create table if not exists public.groups (
  id uuid primary key default gen_random_uuid (),
  name text not null,
  kind text not null default 'trip' check (kind in ('trip','family','team','personal_mirror')),
  created_by uuid not null references auth.users (id) default auth.uid(),
  settings jsonb not null default '{"approval_mode":"all_other_members"}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.group_members (
  group_id uuid not null references public.groups (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  role public.member_role not null default 'member',
  joined_at timestamptz not null default now (),
  primary key (group_id, user_id)
);

create table if not exists public.group_invites (
  id uuid primary key default gen_random_uuid (),
  group_id uuid not null references public.groups (id) on delete cascade,
  code text not null unique,
  created_by uuid not null references auth.users (id),
  expires_at timestamptz,
  created_at timestamptz not null default now ()
);

create table if not exists public.transactions (
  id uuid primary key default gen_random_uuid (),
  submitted_by uuid not null references auth.users (id),
  payer_id uuid not null references auth.users (id),
  group_id uuid references public.groups (id) on delete cascade,
  expense_date date not null,
  amount numeric(14, 2) not null check (amount > 0),
  currency text not null default 'INR',
  category text not null,
  subcategory text not null default '',
  note text not null default '',
  status text not null default 'approved' check (status in ('pending','approved','rejected')),
  created_at timestamptz not null default now (),
  constraint transactions_personal_group_check check (
    (group_id is null and status = 'approved' and submitted_by = payer_id)
    or (group_id is not null)
  )
);

create table if not exists public.transaction_splits (
  transaction_id uuid not null references public.transactions (id) on delete cascade,
  member_id uuid not null references auth.users (id) on delete cascade,
  share_amount numeric(14, 2) not null check (share_amount >= 0),
  primary key (transaction_id, member_id)
);

create table if not exists public.transaction_approvals (
  transaction_id uuid not null references public.transactions (id) on delete cascade,
  voter_id uuid not null references auth.users (id) on delete cascade,
  decision text not null check (decision in ('approve','reject')),
  decided_at timestamptz not null default now (),
  primary key (transaction_id, voter_id)
);

create index if not exists idx_transactions_personal_user on public.transactions (submitted_by, expense_date desc)
  where group_id is null;

create index if not exists idx_transactions_group on public.transactions (group_id, expense_date desc)
  where group_id is not null;

create index if not exists idx_transactions_group_pending on public.transactions (group_id, status);

-- ---------------------------------------------------------------------------
-- RPC: create group + owner membership
-- ---------------------------------------------------------------------------
create or replace function public.fn_create_group (p_name text, p_kind text default 'trip')
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  gid uuid;
begin
  insert into public.groups (name, kind, created_by)
    values (p_name, coalesce(nullif(trim(p_kind), ''), 'trip'), auth.uid())
    returning id into gid;
  insert into public.group_members (group_id, user_id, role)
    values (gid, auth.uid (), 'owner');
  return gid;
end;
$$;

-- ---------------------------------------------------------------------------
-- RPC: invite code
-- ---------------------------------------------------------------------------
create or replace function public.fn_create_group_invite (
  p_group_id uuid,
  p_expires_in_days integer default 7
)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_code text;
  v_days int := greatest(coalesce(nullif(p_expires_in_days, 0), 7), 1);
begin
  if not exists (
    select 1 from public.group_members m
    where m.group_id = p_group_id and m.user_id = auth.uid ())
  then
    raise exception 'not a member of this group';
  end if;
  /* 12 lowercase hex chars */
  v_code := lower(substring(encode(gen_random_bytes (9), 'hex') from 1 for 12));
  insert into public.group_invites (group_id, code, created_by, expires_at)
    values (
      p_group_id,
      v_code,
      auth.uid (),
      now() + (v_days || ' days')::interval
    );
  return v_code;
end;
$$;

-- ---------------------------------------------------------------------------
-- RPC: redeem invite
-- ---------------------------------------------------------------------------
create or replace function public.fn_redeem_group_invite (p_code text)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  iv public.group_invites%rowtype;
begin
  select * into iv from public.group_invites gi
    where gi.code = lower(trim(p_code));
  if not found then
    raise exception 'invalid invite code';
  end if;
  if iv.expires_at is not null and iv.expires_at < now () then
    raise exception 'invite expired';
  end if;
  insert into public.group_members (group_id, user_id, role)
    values (iv.group_id, auth.uid (), 'member')
    on conflict (group_id, user_id) do nothing;
  return iv.group_id;
end;
$$;

-- ---------------------------------------------------------------------------
-- RPC: add equal-split group expense (pending)
-- ---------------------------------------------------------------------------
create or replace function public.fn_add_group_expense (
  p_group_id uuid,
  p_expense_date date,
  p_amount numeric,
  p_category text,
  p_subcategory text default '',
  p_note text default '',
  p_payer_id uuid default null
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_payer uuid;
  v_tid uuid;
  member_ids uuid[];
  n int;
  j int;
  total_cents int;
  per int;
  rem int;
  share_cents int;
  others_needed int := 0;
begin
  if not exists (
    select 1 from public.group_members m
    where m.group_id = p_group_id and m.user_id = auth.uid ())
  then
    raise exception 'not a member of this group';
  end if;

  v_payer := coalesce(p_payer_id, auth.uid());
  if not exists (
    select 1 from public.group_members m
    where m.group_id = p_group_id and m.user_id = v_payer)
  then
    raise exception 'payer is not a group member';
  end if;

  select array_agg(user_id order by user_id)
    into member_ids
    from public.group_members
    where group_id = p_group_id;

  n := coalesce(array_length(member_ids, 1), 0);
  if n < 1 then
    raise exception 'group has no members';
  end if;

  insert into public.transactions (
    submitted_by,
    payer_id,
    group_id,
    expense_date,
    amount,
    category,
    subcategory,
    note,
    status
  )
  values (
    auth.uid(),
    v_payer,
    p_group_id,
    p_expense_date,
    p_amount,
    p_category,
    coalesce(p_subcategory, ''),
    coalesce(p_note, ''),
    'pending'
  )
  returning id into v_tid;

  total_cents := round(p_amount * 100)::int;
  per := total_cents / n;
  rem := total_cents % n;
  for j in 1..n loop
    share_cents := per + (case when j <= rem then 1 else 0 end);
    insert into public.transaction_splits (transaction_id, member_id, share_amount)
      values (v_tid, member_ids[j], (share_cents::numeric / 100));
  end loop;

  /* Solo group: nobody else can approve → auto-approved */
  select count(*)::int into others_needed
    from public.group_members gm
    where gm.group_id = p_group_id and gm.user_id <> auth.uid();
  if others_needed = 0 then
    update public.transactions set status = 'approved' where id = v_tid;
  end if;

  return v_tid;
end;
$$;

-- ---------------------------------------------------------------------------
-- RPC: approve / reject vote
-- ---------------------------------------------------------------------------
create or replace function public.fn_vote_on_transaction (p_transaction_id uuid, p_vote text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_submitter uuid;
  v_group uuid;
  v_status text;
  v_needed int := 0;
  v_have int := 0;
  v_vote text := lower(trim(p_vote));
begin
  select t.submitted_by, t.group_id, t.status into v_submitter, v_group, v_status
  from public.transactions t where t.id = p_transaction_id;
  if not found then
    return jsonb_build_object('status', 'error', 'detail', 'not_found');
  end if;
  if v_group is null then
    return jsonb_build_object('status', 'error', 'detail', 'personal_transaction');
  end if;
  if v_status <> 'pending' then
    return jsonb_build_object('status', 'already_final', 'value', v_status);
  end if;
  if v_vote not in ('approve','reject') then
    return jsonb_build_object('status', 'error', 'detail', 'bad_vote');
  end if;

  if not exists (
    select 1 from public.group_members m
    where m.group_id = v_group and m.user_id = auth.uid ())
  then
    return jsonb_build_object('status', 'error', 'detail', 'not_member');
  end if;

  if v_submitter = auth.uid () then
    return jsonb_build_object('status', 'error', 'detail', 'submitter_cannot_vote');
  end if;

  insert into public.transaction_approvals (transaction_id, voter_id, decision)
    values (p_transaction_id, auth.uid (), v_vote)
    on conflict (transaction_id, voter_id) do update
      set decision = excluded.decision, decided_at = now ();

  if v_vote = 'reject' then
    update public.transactions set status = 'rejected'
      where id = p_transaction_id and status = 'pending';
    return jsonb_build_object('status', 'rejected');
  end if;

  select count(*)::int into v_needed
    from public.group_members gm
    where gm.group_id = v_group and gm.user_id <> v_submitter;

  if v_needed = 0 then
    update public.transactions set status = 'approved'
      where id = p_transaction_id and status = 'pending';
    return jsonb_build_object('status', 'approved');
  end if;

  select count(*)::int into v_have
    from public.transaction_approvals ta
    where ta.transaction_id = p_transaction_id
      and ta.decision = 'approve'
      and exists (
        select 1 from public.group_members gm
        where gm.group_id = v_group
          and gm.user_id = ta.voter_id
          and gm.user_id <> v_submitter
      );

  if v_have >= v_needed then
    update public.transactions set status = 'approved'
      where id = p_transaction_id and status = 'pending';
    return jsonb_build_object('status', 'approved');
  end if;

  return jsonb_build_object('status', 'pending', 'approved_votes', v_have, 'required_votes', v_needed);
end;
$$;

-- ---------------------------------------------------------------------------
-- Grants for RPC (JWT role authenticated)
-- ---------------------------------------------------------------------------
grant execute on function public.fn_create_group (text, text) to authenticated;
grant execute on function public.fn_create_group_invite (uuid, integer) to authenticated;
grant execute on function public.fn_redeem_group_invite (text) to authenticated;
grant execute on function public.fn_add_group_expense (
  uuid, date, numeric, text, text, text, uuid
) to authenticated;
grant execute on function public.fn_vote_on_transaction (uuid, text) to authenticated;

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------
alter table public.groups enable row level security;
alter table public.group_members enable row level security;
alter table public.group_invites enable row level security;
alter table public.transactions enable row level security;
alter table public.transaction_splits enable row level security;
alter table public.transaction_approvals enable row level security;

drop policy if exists groups_select_members on public.groups;
create policy groups_select_members
  on public.groups for select
  using (
    exists (
      select 1 from public.group_members gm
      where gm.group_id = groups.id and gm.user_id = auth.uid ()
    )
  );

drop policy if exists gm_select on public.group_members;
create policy gm_select on public.group_members for select using (
  exists (
    select 1 from public.group_members self
    where self.group_id = group_members.group_id and self.user_id = auth.uid ()
  )
);

-- Invites invisible to normal users (handled by SECURITY DEFINER RPCs).

drop policy if exists trx_personal_insert on public.transactions;
create policy trx_personal_insert on public.transactions for insert with check (
  group_id is null
  and status = 'approved'
  and submitted_by = auth.uid ()
  and payer_id = auth.uid ()
);

drop policy if exists trx_select on public.transactions;
create policy trx_select on public.transactions for select using (
  (group_id is null and submitted_by = auth.uid ())
  or (
    group_id is not null
    and exists (
      select 1 from public.group_members gm
      where gm.group_id = transactions.group_id and gm.user_id = auth.uid ()
    )
  )
);

drop policy if exists trx_update_none on public.transactions;
create policy trx_update_none on public.transactions for update using (false);

drop policy if exists trx_delete_personal on public.transactions;
create policy trx_delete_personal on public.transactions for delete using (
  group_id is null and submitted_by = auth.uid ()
);

drop policy if exists ts_splits_select on public.transaction_splits;
create policy ts_splits_select on public.transaction_splits for select using (
  exists (
    select 1 from public.transactions t
    where t.id = transaction_splits.transaction_id
      and (
        (t.group_id is null and t.submitted_by = auth.uid ())
        or (
          t.group_id is not null and exists (
            select 1 from public.group_members gm
            where gm.group_id = t.group_id and gm.user_id = auth.uid ()
          )
        )
      )
  )
);

drop policy if exists tappr_select on public.transaction_approvals;
create policy tappr_select on public.transaction_approvals for select using (
  exists (
    select 1 from public.transactions t
    where t.id = transaction_approvals.transaction_id
      and t.group_id is not null
      and exists (
        select 1 from public.group_members gm
        where gm.group_id = t.group_id and gm.user_id = auth.uid ()
      )
  )
);
