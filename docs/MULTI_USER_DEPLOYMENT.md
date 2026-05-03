# Multi-User Deployment Guide

## The Problem

**Current Architecture**: Single-user JWT token in environment variable.

```env
SUPABASE_ACCESS_TOKEN=eyJhbGc...  # One user's token
```

**Issue**: If you deploy with YOUR token, everyone uses YOUR account!
- All expenses belong to you
- Other users can't create their own accounts
- No user isolation
- Security nightmare

---

## Solution Options

### Option 1: Token-Per-Request (Recommended for Remote MCP)

Each user provides their own token when connecting to your deployed server.

#### How It Works

1. **Deploy server WITHOUT hardcoded token**
2. **Users pass token in request headers**
3. **Server validates token per-request**

#### Implementation

**Step 1: Modify Server to Accept Token in Headers**

Create `src/expense_mcp/auth_middleware.py`:

```python
"""Authentication middleware for multi-user support."""
from typing import Optional
import os
from contextvars import ContextVar

# Thread-safe storage for current request's token
_current_token: ContextVar[Optional[str]] = ContextVar('current_token', default=None)

def set_request_token(token: str) -> None:
    """Set token for current request."""
    _current_token.set(token)

def get_request_token() -> str:
    """Get token for current request, fallback to env."""
    token = _current_token.get()
    if token:
        return token
    # Fallback to environment variable (for local dev)
    token = os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("No authentication token provided")
    return token

def clear_request_token() -> None:
    """Clear token after request."""
    _current_token.set(None)
```

**Step 2: Update `supabase_client.py`**

```python
"""Supabase client scoped to the current user's JWT (RLS)."""
from __future__ import annotations
import os
from supabase import Client, create_client
from expense_mcp.auth_middleware import get_request_token

def require_access_token() -> str:
    """Get token from request context or environment."""
    return get_request_token()

def get_user_client() -> Client:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not url or not key:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_ANON_KEY in the environment.")
    token = require_access_token()
    client = create_client(url, key)
    client.postgrest.auth(token)
    return client
```

**Step 3: Add HTTP Middleware**

Update `src/expense_mcp/server.py`:

```python
from fastapi import Request, HTTPException
from expense_mcp.auth_middleware import set_request_token, clear_request_token

# Add before tool definitions
@mcp.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Extract token from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]  # Remove "Bearer " prefix
        set_request_token(token)
    
    try:
        response = await call_next(request)
        return response
    finally:
        clear_request_token()
```

#### User Configuration (Claude Desktop)

Each user configures their own token:

```json
{
  "mcpServers": {
    "expense-mcp": {
      "url": "https://your-deployed-server.com",
      "headers": {
        "Authorization": "Bearer eyJhbGc...USER_SPECIFIC_TOKEN"
      }
    }
  }
}
```

#### Pros & Cons

✅ **Pros**:
- True multi-user support
- Each user has their own data
- Secure (RLS enforced)
- Simple deployment

❌ **Cons**:
- Users must get their own Supabase JWT
- Tokens expire (need refresh mechanism)
- Users need Supabase accounts

---

### Option 2: OAuth Flow (Best for Production)

Implement proper OAuth authentication flow.

#### Architecture

```
User → Claude Desktop → Your Server → Supabase Auth
                ↓
         OAuth Login Page
                ↓
         Get JWT Token
                ↓
         Store in Session
```

#### Implementation Steps

**Step 1: Add OAuth Endpoints**

```python
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from supabase import create_client

app = FastAPI()

@app.get("/auth/login")
async def login():
    """Redirect to Supabase OAuth."""
    supabase = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_ANON_KEY"]
    )
    # Redirect to Supabase OAuth page
    auth_url = supabase.auth.sign_in_with_oauth({
        "provider": "google",  # or "github", "email"
        "options": {
            "redirect_to": "https://your-server.com/auth/callback"
        }
    })
    return RedirectResponse(auth_url)

@app.get("/auth/callback")
async def callback(code: str):
    """Handle OAuth callback."""
    supabase = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_ANON_KEY"]
    )
    session = supabase.auth.exchange_code_for_session(code)
    # Store session.access_token in secure cookie/session
    return {"access_token": session.access_token}
```

**Step 2: Enable OAuth in Supabase**

1. Go to Supabase Dashboard → **Authentication** → **Providers**
2. Enable **Google** or **GitHub** OAuth
3. Add OAuth credentials from Google/GitHub Console
4. Set redirect URL: `https://your-server.com/auth/callback`

**Step 3: User Flow**

1. User opens Claude Desktop
2. Claude connects to your server
3. Server returns "Not authenticated" → redirect to login
4. User logs in via Google/GitHub
5. Server gets JWT token
6. Token stored in session
7. All subsequent requests use that token

#### Pros & Cons

✅ **Pros**:
- Professional user experience
- No manual token management
- Automatic token refresh
- Supports multiple OAuth providers

❌ **Cons**:
- Complex implementation
- Requires OAuth setup
- Need session management
- More infrastructure

---

### Option 3: API Key System (Simplest for Sharing)

Generate API keys that map to Supabase users.

#### Architecture

```
User → API Key → Your Server → Maps to Supabase JWT → Database
```

#### Implementation

**Step 1: Create API Keys Table**

```sql
create table public.api_keys (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  api_key text not null unique,
  name text not null,
  created_at timestamptz not null default now(),
  last_used_at timestamptz,
  expires_at timestamptz
);

create index idx_api_keys_key on public.api_keys(api_key);
```

**Step 2: API Key Generation Script**

```python
import secrets
from supabase import create_client

def generate_api_key(user_email: str, key_name: str) -> str:
    """Generate API key for a user."""
    supabase = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"]  # Admin key
    )
    
    # Get user by email
    user = supabase.auth.admin.get_user_by_email(user_email)
    
    # Generate random API key
    api_key = f"exp_{secrets.token_urlsafe(32)}"
    
    # Store in database
    supabase.table("api_keys").insert({
        "user_id": user.id,
        "api_key": api_key,
        "name": key_name
    }).execute()
    
    return api_key

# Usage
api_key = generate_api_key("rahul@example.com", "Claude Desktop")
print(f"Your API key: {api_key}")
```

**Step 3: Validate API Key in Server**

```python
from expense_mcp.auth_middleware import set_request_token

@mcp.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """Validate API key and get user's JWT."""
    api_key = request.headers.get("X-API-Key", "")
    
    if not api_key:
        raise HTTPException(401, "API key required")
    
    # Look up API key in database
    supabase = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )
    
    result = supabase.table("api_keys").select("user_id").eq("api_key", api_key).single().execute()
    
    if not result.data:
        raise HTTPException(401, "Invalid API key")
    
    user_id = result.data["user_id"]
    
    # Generate JWT for this user (using service role)
    jwt_token = supabase.auth.admin.generate_link({
        "type": "magiclink",
        "email": user_id  # This is simplified
    })
    
    set_request_token(jwt_token)
    
    response = await call_next(request)
    return response
```

**Step 4: User Configuration**

```json
{
  "mcpServers": {
    "expense-mcp": {
      "url": "https://your-deployed-server.com",
      "headers": {
        "X-API-Key": "exp_abc123xyz..."
      }
    }
  }
}
```

#### Pros & Cons

✅ **Pros**:
- Simple for users (just one API key)
- No token expiration issues
- Easy to revoke access
- Can track usage per key

❌ **Cons**:
- Need admin script to generate keys
- Extra database table
- Need service role key (security risk if leaked)

---

## Recommended Approach by Use Case

### Personal Use (1-5 users)
**Use**: Option 1 (Token-Per-Request)
- Create Supabase accounts manually
- Share deployment URL
- Each user adds their JWT to Claude config

### Small Team (5-20 users)
**Use**: Option 3 (API Key System)
- You generate API keys for team members
- Simple onboarding (just share API key)
- Easy to manage

### Public/Production (20+ users)
**Use**: Option 2 (OAuth Flow)
- Professional user experience
- Self-service signup
- Automatic token management

---

## Step-by-Step: Deploy for Multiple Users (Option 1)

### Step 1: Update Code

Apply the changes from Option 1 above:
1. Create `auth_middleware.py`
2. Update `supabase_client.py`
3. Add HTTP middleware to `server.py`

### Step 2: Deploy Without Token

**Railway.app**:
```bash
# Environment variables (NO SUPABASE_ACCESS_TOKEN)
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=eyJhbGc...
MCP_HOST=0.0.0.0
MCP_PORT=8000
```

### Step 3: Create User Accounts

For each user:
1. Go to Supabase → **Authentication** → **Users**
2. Click **"Add user"**
3. Enter email/password
4. Enable **"Auto Confirm User"**
5. Send credentials to user

### Step 4: User Gets Their Token

User runs this script:

```python
# get_my_token.py
from supabase import create_client

url = "https://YOUR_PROJECT.supabase.co"
anon_key = "YOUR_ANON_KEY"

email = input("Email: ")
password = input("Password: ")

client = create_client(url, anon_key)
response = client.auth.sign_in_with_password({
    "email": email,
    "password": password
})

print(f"\nYour access token:")
print(response.session.access_token)
print(f"\nExpires at: {response.session.expires_at}")
```

### Step 5: User Configures Claude Desktop

```json
{
  "mcpServers": {
    "expense-mcp": {
      "url": "https://your-deployed-server.com",
      "headers": {
        "Authorization": "Bearer USER_TOKEN_HERE"
      }
    }
  }
}
```

### Step 6: Test

User opens Claude Desktop and tries:
```
Who am I?
Add an expense: ₹500 for lunch
```

---

## Token Refresh Strategy

### Problem
JWT tokens expire after 1 hour.

### Solution: Auto-Refresh Script

**For Users**:

Create `refresh_token.py`:
```python
import os
import json
from supabase import create_client

CONFIG_FILE = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")

# Read current config
with open(CONFIG_FILE) as f:
    config = json.load(f)

# Get credentials
url = "https://YOUR_PROJECT.supabase.co"
anon_key = "YOUR_ANON_KEY"
email = "your-email@example.com"
password = "your-password"

# Get new token
client = create_client(url, anon_key)
response = client.auth.sign_in_with_password({
    "email": email,
    "password": password
})

# Update config
config["mcpServers"]["expense-mcp"]["headers"]["Authorization"] = f"Bearer {response.session.access_token}"

# Write back
with open(CONFIG_FILE, 'w') as f:
    json.dump(config, f, indent=2)

print("Token refreshed! Restart Claude Desktop.")
```

**Run every 50 minutes**:
```bash
# macOS/Linux cron
*/50 * * * * python /path/to/refresh_token.py

# Windows Task Scheduler
# Create task to run every 50 minutes
```

---

## Security Considerations

### ⚠️ Important

1. **Never share your service role key** (only needed for Option 3)
2. **Use HTTPS** for deployed server (Railway/Render provide this)
3. **Validate tokens** on every request (RLS does this)
4. **Rate limiting** to prevent abuse
5. **Audit logs** to track usage

### Rate Limiting

Add to `server.py`:
```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@mcp.tool()
@limiter.limit("100/hour")  # 100 requests per hour per IP
def add_expense(...):
    ...
```

---

## Testing Multi-User Setup

### Test Script

```python
# test_multi_user.py
import requests

SERVER_URL = "https://your-deployed-server.com"

# User 1
user1_token = "eyJhbGc...user1_token"
response1 = requests.post(
    f"{SERVER_URL}/call",
    headers={"Authorization": f"Bearer {user1_token}"},
    json={"tool": "whoami", "arguments": {}}
)
print(f"User 1: {response1.json()}")

# User 2
user2_token = "eyJhbGc...user2_token"
response2 = requests.post(
    f"{SERVER_URL}/call",
    headers={"Authorization": f"Bearer {user2_token}"},
    json={"tool": "whoami", "arguments": {}}
)
print(f"User 2: {response2.json()}")

# Should return different user IDs
assert response1.json()["user_id"] != response2.json()["user_id"]
print("✅ Multi-user isolation working!")
```

---

## FAQ

### Q: Can users sign up themselves?
**A**: Not by default. You need to:
1. Enable email signup in Supabase (Auth → Providers → Email)
2. Implement signup endpoint in your server
3. Or manually create accounts for users

### Q: What if a user's token expires?
**A**: They need to refresh it using the refresh script or re-login.

### Q: Can I use Google/GitHub login?
**A**: Yes! Use Option 2 (OAuth Flow).

### Q: How many users can I support?
**A**: Supabase free tier: unlimited users, 50k API requests/month.

### Q: Is this secure?
**A**: Yes, if you:
- Use HTTPS
- Validate tokens per-request
- Enable RLS policies
- Don't share service role key

---

## Next Steps

1. Choose your deployment option (1, 2, or 3)
2. Implement the code changes
3. Deploy to Railway/Render
4. Create test users
5. Share deployment URL + instructions
6. Monitor usage in Supabase dashboard

For most cases, **start with Option 1** (Token-Per-Request) and upgrade to Option 2 (OAuth) if you get more users.
