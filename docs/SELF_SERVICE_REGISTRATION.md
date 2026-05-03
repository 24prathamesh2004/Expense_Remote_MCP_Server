# Self-Service User Registration

## 🎯 The Solution

**One-time setup tool**: New users call `register_new_user()` to get their own API key!

✅ **Register ONCE** → Works forever
✅ No manual account creation needed
✅ No token expiration issues
✅ No re-login required
✅ Just works automatically every time

---

## How It Works

### Architecture

```
New User → Claude Desktop (with temp config)
    ↓
Calls: register_new_user(email, password)
    ↓
Server → Supabase: Creates user + generates API key
    ↓
Returns: API key
    ↓
User updates Claude config with their API key
    ↓
Done! All future requests use their API key
```

### Key Features

✅ **Self-service**: No admin intervention needed
✅ **No token expiration**: API keys don't expire
✅ **Secure**: Each user has isolated data (RLS)
✅ **Simple**: One tool call, done
✅ **Revocable**: Users can revoke compromised keys

---

## Setup Instructions

### Step 1: Deploy Server

Deploy with **NO** `SUPABASE_ACCESS_TOKEN` in environment:

```env
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=eyJhbGc...
MCP_HOST=0.0.0.0
MCP_PORT=8000
# NO SUPABASE_ACCESS_TOKEN!
```

### Step 2: Run Migration

In Supabase SQL Editor:

```sql
-- Run: supabase/migrations/007_self_service_registration.sql
```

This creates:
- `api_keys` table
- `fn_register_user()` RPC
- `fn_login_get_key()` RPC
- `fn_validate_api_key()` RPC
- `fn_revoke_api_key()` RPC

### Step 3: Add API Key Middleware

Update `src/expense_mcp/server.py`:

```python
from fastapi import Request, HTTPException
from expense_mcp.supabase_client import get_anon_client

@mcp.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """Validate API key and inject user context."""
    
    # Skip auth for registration/login endpoints
    if request.url.path in ["/health", "/tools"]:
        return await call_next(request)
    
    # Get API key from header
    api_key = request.headers.get("X-API-Key", "")
    
    if not api_key:
        # Fallback to env token (for backward compatibility)
        token = os.environ.get("SUPABASE_ACCESS_TOKEN", "")
        if token:
            os.environ["_REQUEST_TOKEN"] = token
            return await call_next(request)
        
        raise HTTPException(401, "API key required. Use register_new_user tool first.")
    
    # Validate API key
    client = get_anon_client()
    result = client.rpc("fn_validate_api_key", {"p_api_key": api_key}).execute()
    
    if not result.data or result.data.get("status") != "success":
        raise HTTPException(401, "Invalid or expired API key")
    
    user_id = result.data["user_id"]
    
    # Generate temporary JWT for this request
    # (In production, cache this for performance)
    auth_response = client.auth.admin.generate_link({
        "type": "magiclink",
        "email": result.data["email"]
    })
    
    # Inject token into request context
    os.environ["_REQUEST_TOKEN"] = auth_response.properties.access_token
    
    response = await call_next(request)
    
    # Clean up
    if "_REQUEST_TOKEN" in os.environ:
        del os.environ["_REQUEST_TOKEN"]
    
    return response
```

Update `supabase_client.py`:

```python
def require_access_token() -> str:
    # Check request context first
    token = os.environ.get("_REQUEST_TOKEN", "")
    if token:
        return token
    
    # Fallback to environment
    token = os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("No authentication token")
    return token
```

---

## User Onboarding Flow

### For New Users

**Step 1: Initial Claude Desktop Config (Temporary)**

```json
{
  "mcpServers": {
    "expense-mcp-register": {
      "url": "https://your-deployed-server.com",
      "headers": {
        "X-API-Key": "TEMP_REGISTRATION_KEY"
      }
    }
  }
}
```

**Note**: You can use a special registration-only API key, or allow unauthenticated access to `register_new_user` tool.

**Step 2: User Calls Registration Tool**

In Claude Desktop:

```
I want to register for the expense tracker.

Email: rahul@example.com
Password: MySecurePass123
Full Name: Rahul Kumar
```

Claude calls:
```python
register_new_user(
    email="rahul@example.com",
    password="MySecurePass123",
    full_name="Rahul Kumar"
)
```

**Response**:
```json
{
  "status": "success",
  "user_id": "uuid-here",
  "email": "rahul@example.com",
  "api_key": "exp_abc123xyz789...",
  "message": "Registration successful! Save your API key - it will not be shown again."
}
```

**Step 3: User Updates Config**

```json
{
  "mcpServers": {
    "expense-mcp": {
      "url": "https://your-deployed-server.com",
      "headers": {
        "X-API-Key": "exp_abc123xyz789..."
      }
    }
  }
}
```

**Step 4: Restart Claude Desktop**

Done! User can now use all expense tools.

**🎉 That's it! This setup is permanent.**

From now on, every time the user opens Claude Desktop:
- API key is automatically sent with every request
- No login needed
- No re-registration needed
- Just talk naturally: "Add expense ₹500 for lunch"

**User only needs to register again if**:
- They get a new computer
- They delete their Claude config
- They revoke their API key for security reasons

---

## For Existing Users (Lost API Key)

**Step 1: Call Login Tool**

```
I lost my API key. Can you help me get a new one?

Email: rahul@example.com
Password: MySecurePass123
```

Claude calls:
```python
login_get_api_key(
    email="rahul@example.com",
    password="MySecurePass123"
)
```

**Response**:
```json
{
  "status": "success",
  "user_id": "uuid-here",
  "email": "rahul@example.com",
  "api_key": "exp_new_key_here...",
  "message": "Login successful! Use this API key in your Claude Desktop config."
}
```

**Step 2: Update Config & Restart**

---

## Security Features

### Password Requirements

- Minimum 8 characters
- Stored as bcrypt hash
- Never returned in responses

### API Key Format

```
exp_<base64_random_32_bytes>
Example: exp_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
```

### Rate Limiting (Recommended)

Add to prevent abuse:

```python
from slowapi import Limiter

limiter = Limiter(key_func=get_remote_address)

@mcp.tool()
@limiter.limit("5/hour")  # Max 5 registrations per hour per IP
def register_new_user(...):
    ...
```

### Email Verification (Optional)

For production, add email verification:

```sql
-- Add to fn_register_user
-- Send verification email via Supabase Auth
select auth.send_magic_link(p_email);
```

---

## Admin Tools

### View All Users

```sql
select 
  u.id,
  u.email,
  u.created_at,
  count(ak.id) as api_key_count
from auth.users u
left join public.api_keys ak on ak.user_id = u.id and ak.is_active = true
group by u.id, u.email, u.created_at
order by u.created_at desc;
```

### Revoke User's API Keys (Admin)

```sql
update public.api_keys
set is_active = false
where user_id = 'user-uuid-here';
```

### Delete User (Admin)

```sql
-- This cascades to api_keys, transactions, etc.
delete from auth.users where id = 'user-uuid-here';
```

---

## Sharing Instructions

### What to Share with New Users

**1. Server URL**
```
https://your-expense-mcp.railway.app
```

**2. Initial Setup Instructions**

```markdown
# Expense MCP Setup

## Step 1: Add to Claude Desktop

Edit your `claude_desktop_config.json`:

{
  "mcpServers": {
    "expense-mcp": {
      "url": "https://your-expense-mcp.railway.app"
    }
  }
}

## Step 2: Register (One-time)

Restart Claude Desktop and say:

"Register me for expense tracker:
- Email: your-email@example.com
- Password: YourSecurePassword123
- Name: Your Name"

## Step 3: Save Your API Key

Claude will show you an API key like:
exp_abc123xyz...

**SAVE THIS!** You'll need it in the next step.

## Step 4: Update Config with Your API Key

Edit `claude_desktop_config.json` again:

{
  "mcpServers": {
    "expense-mcp": {
      "url": "https://your-expense-mcp.railway.app",
      "headers": {
        "X-API-Key": "exp_YOUR_KEY_HERE"
      }
    }
  }
}

## Step 5: Restart Claude Desktop

Done! Try: "Add an expense: ₹500 for lunch"
```

---

## Troubleshooting

### "User already exists"

**Solution**: Use `login_get_api_key` instead:

```
Get my API key:
Email: rahul@example.com
Password: MyPassword123
```

### "Invalid email or password"

**Solution**: Check credentials or register new account.

### "API key required"

**Solution**: 
1. Make sure you updated `claude_desktop_config.json`
2. Restart Claude Desktop
3. Check API key is correct (no extra spaces)

### "Invalid or expired API key"

**Solution**:
1. Login again to get new key: `login_get_api_key(...)`
2. Or check if key was revoked
3. Update config with new key

---

## Advanced: Registration Without Initial Config

### Option A: Public Registration Endpoint

Allow unauthenticated access to registration:

```python
@mcp.middleware("http")
async def api_key_middleware(request: Request, call_next):
    # Allow registration without auth
    if request.url.path == "/call":
        body = await request.json()
        if body.get("tool") in ["register_new_user", "login_get_api_key"]:
            return await call_next(request)
    
    # ... rest of auth logic
```

Users can register without any initial config!

### Option B: Shared Registration Key

Create one special API key for registration only:

```sql
-- Create a registration-only user
insert into auth.users (email, ...) values ('registration@system', ...);

-- Generate registration key
insert into public.api_keys (user_id, api_key, key_name)
values ('registration-user-id', 'exp_REGISTRATION_KEY', 'Public Registration');
```

Share this key publicly for registration:

```json
{
  "mcpServers": {
    "expense-mcp": {
      "url": "https://your-server.com",
      "headers": {
        "X-API-Key": "exp_REGISTRATION_KEY"
      }
    }
  }
}
```

---

## Migration from Old System

### If Users Already Have Supabase Accounts

```sql
-- Generate API keys for existing users
insert into public.api_keys (user_id, api_key, key_name)
select 
  id,
  'exp_' || encode(gen_random_bytes(32), 'base64'),
  'Migrated Key'
from auth.users
where id not in (select user_id from public.api_keys);
```

Send users their API keys via email.

---

## Monitoring

### Track Registrations

```sql
select 
  date_trunc('day', created_at) as day,
  count(*) as new_users
from auth.users
group by day
order by day desc;
```

### Track API Key Usage

```sql
select 
  u.email,
  ak.key_name,
  ak.last_used_at,
  ak.created_at
from public.api_keys ak
join auth.users u on u.id = ak.user_id
where ak.is_active = true
order by ak.last_used_at desc nulls last;
```

### Inactive Users

```sql
select 
  u.email,
  u.created_at,
  max(ak.last_used_at) as last_active
from auth.users u
left join public.api_keys ak on ak.user_id = u.id
group by u.id, u.email, u.created_at
having max(ak.last_used_at) < now() - interval '30 days'
  or max(ak.last_used_at) is null;
```

---

## Cost Estimation

### Supabase Free Tier

- **Users**: Unlimited
- **API Requests**: 50,000/month
- **Storage**: 500 MB

**Estimate**: 
- 100 users × 50 requests/day = 150k requests/month
- Need Pro tier (~₹2000/month)

### Scaling

- **1-50 users**: Free tier
- **50-500 users**: Pro tier (₹2000/month)
- **500+ users**: Team tier (₹8000/month)

---

## Next Steps

1. ✅ Run migration 007
2. ✅ Add API key middleware
3. ✅ Deploy server
4. ✅ Test registration flow
5. ✅ Share instructions with users
6. 📊 Monitor usage
7. 🔒 Add rate limiting
8. 📧 (Optional) Add email verification

**Result**: Users can self-register in 2 minutes! 🎉
