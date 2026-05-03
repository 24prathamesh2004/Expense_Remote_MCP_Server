-- Optional: copy rows from legacy public.expenses into public.transactions (personal ledger).
-- Run once after public.transactions exists. Skip if table expenses is empty/missing.

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
select
  e.user_id,
  e.user_id,
  null,
  e.date,
  e.amount,
  e.category,
  e.subcategory,
  e.note,
  'approved'
from public.expenses e
where not exists (
  select 1 from public.transactions t
    where t.submitted_by = e.user_id
      and t.group_id is null
      and t.expense_date = e.date
      and t.amount = e.amount
      and t.note = coalesce(e.note, '')
      limit 1
);
