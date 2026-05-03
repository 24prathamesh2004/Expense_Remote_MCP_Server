-- Security audit and hardening
-- Ensures RLS policies are comprehensive and prevent unauthorized access

-- ---------------------------------------------------------------------------
-- Audit: Verify all tables have RLS enabled
-- ---------------------------------------------------------------------------
do $$
declare
  tbl record;
begin
  for tbl in
    select schemaname, tablename
    from pg_tables
    where schemaname = 'public'
      and tablename in (
        'groups', 'group_members', 'group_invites',
        'transactions', 'transaction_splits', 'transaction_approvals',
        'settlement_payments'
      )
  loop
    if not exists (
      select 1 from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = tbl.schemaname
        and c.relname = tbl.tablename
        and c.relrowsecurity = true
    ) then
      raise exception 'RLS not enabled on %.%', tbl.schemaname, tbl.tablename;
    end if;
  end loop;
end;
$$;

-- ---------------------------------------------------------------------------
-- Enhanced RLS: Prevent cross-group data leakage
-- ---------------------------------------------------------------------------

-- Transactions: ensure group_id isolation
drop policy if exists trx_select on public.transactions;
create policy trx_select on public.transactions for select using (
  -- Personal transactions: only submitter can see
  (group_id is null and submitted_by = auth.uid())
  or
  -- Group transactions: only group members can see
  (
    group_id is not null
    and exists (
      select 1 from public.group_members gm
      where gm.group_id = transactions.group_id
        and gm.user_id = auth.uid()
    )
  )
);

-- Transaction splits: only visible to group members
drop policy if exists ts_splits_select on public.transaction_splits;
create policy ts_splits_select on public.transaction_splits for select using (
  exists (
    select 1 from public.transactions t
    left join public.group_members gm on gm.group_id = t.group_id
    where t.id = transaction_splits.transaction_id
      and (
        -- Personal transaction: submitter only
        (t.group_id is null and t.submitted_by = auth.uid())
        or
        -- Group transaction: member only
        (t.group_id is not null and gm.user_id = auth.uid())
      )
  )
);

-- Transaction approvals: only visible to group members
drop policy if exists tappr_select on public.transaction_approvals;
create policy tappr_select on public.transaction_approvals for select using (
  exists (
    select 1 from public.transactions t
    join public.group_members gm on gm.group_id = t.group_id
    where t.id = transaction_approvals.transaction_id
      and gm.user_id = auth.uid()
  )
);

-- Groups: only members can see
drop policy if exists groups_select_members on public.groups;
create policy groups_select_members on public.groups for select using (
  exists (
    select 1 from public.group_members gm
    where gm.group_id = groups.id
      and gm.user_id = auth.uid()
  )
);

-- Group members: only co-members can see
drop policy if exists gm_select on public.group_members;
create policy gm_select on public.group_members for select using (
  exists (
    select 1 from public.group_members self
    where self.group_id = group_members.group_id
      and self.user_id = auth.uid()
  )
);

-- Settlement payments: only group members can see
drop policy if exists settlements_select on public.settlement_payments;
create policy settlements_select on public.settlement_payments for select using (
  exists (
    select 1 from public.group_members gm
    where gm.group_id = settlement_payments.group_id
      and gm.user_id = auth.uid()
  )
);

-- ---------------------------------------------------------------------------
-- Prevent direct modifications (force RPC usage)
-- ---------------------------------------------------------------------------

-- Groups: no direct insert (use fn_create_group)
drop policy if exists groups_insert_none on public.groups;
create policy groups_insert_none on public.groups for insert with check (false);

-- Group members: no direct insert (use fn_redeem_group_invite or fn_create_group)
drop policy if exists gm_insert_none on public.group_members;
create policy gm_insert_none on public.group_members for insert with check (false);

-- Group invites: no direct access (use RPCs)
drop policy if exists gi_select_none on public.group_invites;
create policy gi_select_none on public.group_invites for select using (false);

drop policy if exists gi_insert_none on public.group_invites;
create policy gi_insert_none on public.group_invites for insert with check (false);

-- Transaction splits: no direct modification (managed by fn_add_group_expense)
drop policy if exists ts_insert_none on public.transaction_splits;
create policy ts_insert_none on public.transaction_splits for insert with check (false);

drop policy if exists ts_update_none on public.transaction_splits;
create policy ts_update_none on public.transaction_splits for update using (false);

drop policy if exists ts_delete_none on public.transaction_splits;
create policy ts_delete_none on public.transaction_splits for delete using (false);

-- Transaction approvals: no direct modification (use fn_vote_on_transaction)
drop policy if exists tappr_insert_none on public.transaction_approvals;
create policy tappr_insert_none on public.transaction_approvals for insert with check (false);

drop policy if exists tappr_update_none on public.transaction_approvals;
create policy tappr_update_none on public.transaction_approvals for update using (false);

drop policy if exists tappr_delete_none on public.transaction_approvals;
create policy tappr_delete_none on public.transaction_approvals for delete using (false);

-- ---------------------------------------------------------------------------
-- Audit logging (optional but recommended)
-- ---------------------------------------------------------------------------
create table if not exists public.audit_log (
  id uuid primary key default gen_random_uuid(),
  table_name text not null,
  operation text not null check (operation in ('INSERT', 'UPDATE', 'DELETE')),
  user_id uuid references auth.users (id),
  record_id uuid,
  old_data jsonb,
  new_data jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_audit_log_table on public.audit_log (table_name, created_at desc);
create index if not exists idx_audit_log_user on public.audit_log (user_id, created_at desc);

alter table public.audit_log enable row level security;

-- Only admins can read audit logs (adjust as needed)
create policy audit_log_admin_only on public.audit_log for select using (false);

-- Audit trigger function
create or replace function public.audit_trigger_func()
returns trigger
language plpgsql
security definer
as $$
begin
  if TG_OP = 'DELETE' then
    insert into public.audit_log (table_name, operation, user_id, record_id, old_data)
    values (TG_TABLE_NAME, TG_OP, auth.uid(), OLD.id, to_jsonb(OLD));
    return OLD;
  elsif TG_OP = 'UPDATE' then
    insert into public.audit_log (table_name, operation, user_id, record_id, old_data, new_data)
    values (TG_TABLE_NAME, TG_OP, auth.uid(), NEW.id, to_jsonb(OLD), to_jsonb(NEW));
    return NEW;
  elsif TG_OP = 'INSERT' then
    insert into public.audit_log (table_name, operation, user_id, record_id, new_data)
    values (TG_TABLE_NAME, TG_OP, auth.uid(), NEW.id, to_jsonb(NEW));
    return NEW;
  end if;
  return null;
end;
$$;

-- Apply audit triggers to sensitive tables
drop trigger if exists audit_transactions on public.transactions;
create trigger audit_transactions
  after insert or update or delete on public.transactions
  for each row execute function audit_trigger_func();

drop trigger if exists audit_settlements on public.settlement_payments;
create trigger audit_settlements
  after insert or update or delete on public.settlement_payments
  for each row execute function audit_trigger_func();

drop trigger if exists audit_approvals on public.transaction_approvals;
create trigger audit_approvals
  after insert or update or delete on public.transaction_approvals
  for each row execute function audit_trigger_func();

-- ---------------------------------------------------------------------------
-- Security validation queries (run manually to verify)
-- ---------------------------------------------------------------------------

-- Verify no tables are missing RLS
comment on schema public is 'Security audit: All tables with user data must have RLS enabled';

-- Test query: ensure user can only see their own groups
-- select * from public.groups; -- should only return groups where user is member

-- Test query: ensure user cannot see other users' personal transactions
-- select * from public.transactions where group_id is null; -- should only return own

-- Test query: ensure user cannot vote on transactions outside their groups
-- select * from public.transaction_approvals; -- should only return approvals in user's groups
