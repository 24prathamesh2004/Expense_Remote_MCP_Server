# Expense MCP Architecture

## What This Actually Is

**Not just an expense tracker.**

This is a **collaborative financial workflow engine** exposed via Model Context Protocol (MCP). It enables AI assistants to orchestrate complex multi-party financial workflows with proper approval mechanisms, balance tracking, and settlement coordination.

---

## Core Architecture Decisions

### 1. **Unified Transaction Ledger**

**Design**: Single `transactions` table for both personal and group expenses.

```sql
transactions (
  id,
  submitted_by,
  payer_id,
  group_id,        -- NULL = personal, UUID = group
  expense_date,
  amount,
  status,          -- 'pending' | 'approved' | 'rejected'
  ...
)
```

**Why**:
- Simplifies queries (one table to join)
- Enables future features (convert personal → group)
- Consistent audit trail
- RLS policies apply uniformly

**Constraint**: Personal expenses must have `group_id IS NULL` and `status = 'approved'`.

---

### 2. **Security Model: RLS-First**

**Critical**: Client-side JWT auth is NOT security. All security is enforced via **Row Level Security (RLS)** policies.

**Layers**:
1. **JWT Authentication**: User identity via `SUPABASE_ACCESS_TOKEN`
2. **RLS Policies**: Database-level access control
3. **Security Definer RPCs**: Controlled mutations via stored procedures

**Example RLS Policy**:
```sql
create policy trx_select on transactions for select using (
  (group_id is null and submitted_by = auth.uid())
  or (
    group_id is not null
    and exists (
      select 1 from group_members gm
      where gm.group_id = transactions.group_id
        and gm.user_id = auth.uid()
    )
  )
);
```

**Why Security Definer RPCs**:
- Atomic operations (splits + approvals + status)
- Business logic enforcement (can't vote on own submission)
- Prevents partial state corruption

---

### 3. **Approval Workflow: All-Other-Members**

**Rule**: Group expense requires approval from **every member except submitter**.

**State Machine**:
```
pending → approved (all others approve)
pending → rejected (any member rejects)
```

**Edge Cases**:
- Solo group: auto-approve (no other voters)
- Submitter cannot vote on own expense
- Rejection is final (no recovery)

**Implementation**: `fn_vote_on_transaction` RPC counts approvals and transitions state.

---

### 4. **Performance Optimization: Materialized Views**

**Problem**: `_with_pending_hint()` was doing N+1 queries on every tool call.

**Solution**: Materialized view `mv_user_pending_approvals` with trigger-based refresh.

**Before**:
```python
# Every tool call:
1. Query transactions (status='pending')
2. Query transaction_approvals
3. Filter in Python
```

**After**:
```python
# Every tool call:
1. Single RPC: fn_get_pending_count(user_id)
   → reads pre-computed materialized view
```

**Refresh Strategy**:
- Triggers on `transactions` (INSERT, UPDATE status)
- Triggers on `transaction_approvals` (INSERT, UPDATE)
- `REFRESH MATERIALIZED VIEW CONCURRENTLY` (non-blocking)

**Trade-off**: Slight staleness (milliseconds) for 10-100x query reduction.

---

### 5. **Settlement Recording: Closing the Loop**

**Problem**: `simplify_group_debts()` only suggests payments. Balances never reduce.

**Solution**: `settlement_payments` table + `fn_record_settlement` RPC.

**Workflow**:
1. `simplify_group_debts()` → suggests: "Alice pays Bob $50"
2. Alice pays Bob via Venmo (real world)
3. `record_settlement(group_id, alice, bob, 50)` → records payment
4. `group_balances()` → now includes settlement adjustments

**Balance Calculation**:
```sql
net_balance = 
  (expenses_paid - expenses_owed)  -- original logic
  - settlements_sent                -- new
  + settlements_received            -- new
```

**Why Separate Table**:
- Settlements are not expenses (different semantics)
- Enables settlement history/audit
- Can be reversed/disputed separately

---

### 6. **Equal Split Logic: Paisa-Level Precision**

**Challenge**: ₹100.00 split 3 ways = ₹33.33 + ₹33.33 + ₹33.34 (1 paisa remainder).

**Algorithm**:
```python
total_paise = round(amount * 100)  # Convert rupees to paise
per_member = total_paise // n
remainder = total_paise % n

for i in range(n):
    share = per_member + (1 if i < remainder else 0)
```

**Result**: First `remainder` members get +1 paisa. Total always exact.

**Why Not Floating Point**: Avoids rounding errors in balance calculations.

**Example**: ₹1000 split among 3 members = ₹333.33 + ₹333.33 + ₹333.34

---

## Data Flow

### Adding a Group Expense

```
User → MCP Tool: add_group_expense()
  ↓
Python: validate, call fn_add_group_expense RPC
  ↓
PostgreSQL RPC:
  1. Verify group membership
  2. INSERT transaction (status='pending')
  3. Calculate equal splits (cent-precise)
  4. INSERT transaction_splits (one per member)
  5. Check if solo group → auto-approve
  6. RETURN transaction_id
  ↓
Trigger: refresh_pending_approvals_mv()
  ↓
Python: return with pending_approvals_summary
```

### Voting on Expense

```
User → MCP Tool: approve_group_expense(tid)
  ↓
Python: call fn_vote_on_transaction(tid, 'approve')
  ↓
PostgreSQL RPC:
  1. Verify voter is group member
  2. Verify voter ≠ submitter
  3. UPSERT transaction_approvals
  4. Count approvals vs required
  5. If all approved → UPDATE status='approved'
  6. RETURN vote_result
  ↓
Trigger: refresh_pending_approvals_mv()
  ↓
Python: return with updated pending summary
```

---

## Security Hardening (Migration 006)

### Principle: Defense in Depth

1. **RLS on All Tables**: No table accessible without policy
2. **No Direct Writes**: Critical tables force RPC usage
3. **Audit Logging**: All mutations logged to `audit_log`
4. **Group Isolation**: Users cannot see other groups' data

### Prevented Attacks

**Cross-Group Data Leakage**:
```sql
-- Attacker tries to read another group's transactions
select * from transactions where group_id = 'other-group-uuid';
-- RLS blocks: user not in group_members
```

**Approval Bypass**:
```sql
-- Attacker tries to directly approve own expense
insert into transaction_approvals (transaction_id, voter_id, decision)
values ('my-expense', auth.uid(), 'approve');
-- RLS blocks: insert policy = false (must use RPC)
```

**Balance Manipulation**:
```sql
-- Attacker tries to fake a settlement
insert into settlement_payments (group_id, from_user_id, to_user_id, amount)
values ('group', 'victim', auth.uid(), 1000);
-- RLS blocks: insert policy = false (must use RPC)
```

---

## Future Architecture Considerations

### 1. **Unequal Splits**

**Current**: Hardcoded equal splits in `fn_add_group_expense`.

**Future**:
```sql
create table transaction_split_rules (
  transaction_id uuid,
  member_id uuid,
  split_type text,  -- 'equal' | 'percentage' | 'fixed'
  split_value numeric
);
```

**Use Cases**:
- "Alice pays $100, split 70/30 with Bob"
- "Dinner for 4, but Alice had appetizer (+$15)"

### 2. **Recurring Expenses**

**Pattern**: Monthly rent, subscriptions.

**Approach**:
```sql
create table recurring_templates (
  id uuid,
  group_id uuid,
  schedule text,  -- cron expression
  amount numeric,
  category text,
  auto_approve boolean
);
```

**Trigger**: Scheduled job creates transactions from templates.

### 3. **Multi-Currency Support**

**Current**: Hardcoded `currency = 'INR'` (Indian Rupees).

**Future**:
- Store exchange rates in `currency_rates` table
- Convert to base currency (INR) for balance calculations
- Display in user's preferred currency
- Support common currencies: USD, EUR, GBP, AED, SGD

### 4. **Receipt Parsing Integration**

**Architecture Ready**: MCP tools can be chained by AI.

**Workflow**:
```
User: "Split this restaurant bill among family"
  ↓
AI: OCR receipt → extract items
  ↓
AI: call add_group_expense() for each item
  ↓
AI: call simplify_group_debts()
  ↓
AI: "Rahul should pay Priya ₹450 via UPI"
```

**No code changes needed**: MCP orchestration layer handles it.

**India-Specific**: Works great with Indian restaurant bills, grocery receipts, and UPI payment screenshots.

### 5. **Dispute Resolution**

**Current**: Rejection is final.

**Future**:
```sql
create table transaction_disputes (
  transaction_id uuid,
  raised_by uuid,
  reason text,
  status text,  -- 'open' | 'resolved' | 'escalated'
  resolution text
);
```

**Workflow**: Rejected → dispute → discussion → re-vote or cancel.

---

## Performance Characteristics

### Query Complexity

| Operation | Queries | Complexity |
|-----------|---------|------------|
| `add_expense` (personal) | 1 INSERT | O(1) |
| `add_group_expense` | 1 RPC (3 INSERTs) | O(n) members |
| `approve_group_expense` | 1 RPC (1 UPSERT + count) | O(n) members |
| `group_balances` | 1 RPC (1 aggregate) | O(t) transactions |
| `simplify_group_debts` | 1 RPC + Python | O(n log n) |
| `_with_pending_hint` | 1 RPC (materialized view) | O(1) |

### Scalability Limits

**Group Size**: Tested up to 50 members. Equal splits scale O(n).

**Transaction Volume**: Indexes on `(group_id, expense_date)` support 10k+ transactions per group.

**Pending Approvals**: Materialized view refresh is O(pending_count), typically <100ms.

**Bottleneck**: Settlement simplification is O(n²) worst-case, but n (members) is typically <20.

---

## Testing Strategy

### Unit Tests (Python)
- `settlements.py`: Balance accumulation, debt simplification
- `jwt_sub.py`: JWT parsing edge cases

### Integration Tests (SQL)
- RLS policy enforcement
- RPC business logic (approval counting, auto-approve)
- Trigger-based materialized view refresh

### Security Tests
- Cross-group access attempts
- Direct table manipulation attempts
- SQL injection in RPC parameters

### Load Tests
- 1000 concurrent `add_group_expense` calls
- Materialized view refresh under load
- Balance calculation with 10k transactions

---

## Deployment Checklist

1. **Run Migrations in Order**:
   - `001_expenses.sql` (optional legacy)
   - `002_collaborative_finance.sql` (required)
   - `003_migrate_legacy_expenses.sql` (optional)
   - `004_pending_approvals_optimization.sql` (performance)
   - `005_settlement_recording.sql` (settlements)
   - `006_security_audit.sql` (hardening)

2. **Verify RLS Enabled**:
   ```sql
   select tablename, rowsecurity
   from pg_tables
   where schemaname = 'public';
   ```

3. **Test Security**:
   - Create two users
   - Verify user A cannot see user B's personal expenses
   - Verify user A cannot see groups they're not in

4. **Configure Environment**:
   - `SUPABASE_URL`, `SUPABASE_ANON_KEY`
   - `SUPABASE_ACCESS_TOKEN` (user JWT, refresh periodically)

5. **Monitor Performance**:
   - Materialized view refresh time
   - RPC execution time (should be <100ms)

---

## Why This Architecture Matters

**Traditional Approach**: Client-side logic, API endpoints, manual security checks.

**This Approach**: Database-enforced security, atomic operations, AI-orchestratable.

**Result**: AI assistants can safely manage complex financial workflows without risking data corruption or unauthorized access.

**Key Insight**: MCP + RLS + Security Definer RPCs = trustworthy AI financial automation.
