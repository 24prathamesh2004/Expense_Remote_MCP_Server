# Expense MCP (Supabase — personal + groups)

[FastMCP](https://github.com/jlowin/fastmcp) server backed by **Supabase Postgres** + **RLS**, with **personal** expenses and **group** flows: invites, pending approvals (**all other members must approve**), equal splits, balances, simplified settlement suggestions.

**🇮🇳 India-Centric**: Default currency INR (₹), categories include UPI/PhonePe/GPay, auto/rickshaw, tiffin service, and more.

## 📚 Documentation

- **[👤 User Quick Start](docs/USER_QUICK_START.md)** - For end users: 2-minute setup, no technical knowledge needed
- **[Setup Guide](docs/SETUP_GUIDE.md)** - For developers: database setup, local dev, deployment, Claude connection
- **[Architecture](docs/ARCHITECTURE.md)** - Design decisions, security model, performance optimizations
- **[India-Specific Features](docs/INDIA_SPECIFIC.md)** - INR currency, UPI payments, Indian categories, regional customizations
- **[Multi-User Deployment](docs/MULTI_USER_DEPLOYMENT.md)** - Deploy for multiple users, authentication strategies, token management
- **[Self-Service Registration](docs/SELF_SERVICE_REGISTRATION.md)** - How the one-time registration system works

## Migrations

In Supabase **SQL Editor**, run in order:

1. **`supabase/migrations/001_expenses.sql`** *(optional legacy — only if you already use `public.expenses`)*  
2. **`supabase/migrations/002_collaborative_finance.sql`** *(required — `groups`, `transactions`, splits, approvals, RPCs)*  
3. **`supabase/migrations/003_migrate_legacy_expenses.sql`** *(optional — copy legacy `expenses` → `transactions` once)*  
4. **`supabase/migrations/004_pending_approvals_optimization.sql`** *(recommended — materialized view for performance)*  
5. **`supabase/migrations/005_settlement_recording.sql`** *(required — settlement payments to reduce balances)*  
6. **`supabase/migrations/006_security_audit.sql`** *(recommended — RLS hardening + audit logging)*  

Personal spending is stored in **`public.transactions`** with `group_id IS NULL` and **`submitted_by` / `payer_id`** equal to your user (`whoami`). Group expenses use RPCs (`fn_*`) so splits and approvals stay consistent.

## Quick Start

See **[docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md)** for detailed instructions.

**TL;DR**:

1. Create Supabase project and run migrations
2. Get user JWT token from Supabase
3. Configure environment:
   ```bash
   cp .env.example .env
   # Edit .env with your Supabase credentials
   ```
4. Install and run:
   ```bash
   pip install -e .
   python -m expense_mcp.server
   ```
5. Connect to Claude Desktop (see setup guide)

## End-to-end group flow

1. **User A**: `whoami`, `create_group` (trip/family/team/etc.).  
2. **User A**: `create_group_invite` → share `invite_code` with User B (out-of-band).  
3. **User B**: `redeem_group_invite` with that code (`list_my_groups` confirms).  
4. **User A**: `add_group_expense` → row is **`pending`**; equal **splits** on all members.  
5. **User B**: any tool wraps `pending_approvals_summary`; or call `list_my_pending_approvals`.  
6. **User B**: `approve_group_expense` (wrapper over `vote_on_transaction(..., approve)`). When **every other member** has approved → status **`approved`**.  
7. **Anyone in group**: `group_balances` then `simplify_group_debts` (approved transactions only).

`reject_group_expense` rejects and finalizes **`rejected`**.

If you are alone in a group, inserts **auto‑approve** (no other voter exists).

## Tools (overview)

### Personal

- `add_expense`, `list_expenses`, `summarize` — `transactions` without `group_id`.

### Groups

- `create_group`, `list_my_groups`, `create_group_invite`, `redeem_group_invite`, `list_group_members`  
- `add_group_expense`, `list_group_transactions`, `list_pending_group_expenses`  
- `vote_on_transaction`, `approve_group_expense`, `reject_group_expense`  
- `list_my_pending_approvals`  
- `group_balances`, `simplify_group_debts`  

### Settlements

- `record_settlement` — record real-world payment between members (reduces balances)
- `list_group_settlements` — view settlement payment history

### Utilities

- `whoami`

## Resource

- `expense:///categories` — JSON categories file or built-in defaults.

## Notes

- Response envelope may include **`pending_approvals_summary`** so the assistant can prompt users without push notifications.  
- Pass **real user JWT**s; pasted tokens expire — wire OAuth later.  
- `simplify_group_debts` uses a greedy min-cash-flow heuristic; totals should match **`group_balances`**.
- After real-world payments (Venmo, cash, etc.), use `record_settlement` to update balances.
- All security is enforced via **Row Level Security (RLS)** — client-side auth is NOT sufficient.

## Architecture

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for detailed design decisions, security model, performance optimizations, and future considerations.
