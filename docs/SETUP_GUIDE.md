# Complete Setup Guide: Expense MCP

This guide walks you through setting up the Expense MCP server from scratch, including database creation, local development, deployment, and connecting to Claude Desktop.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Database Setup (Supabase)](#database-setup-supabase)
3. [Local Development](#local-development)
4. [Testing the Server](#testing-the-server)
5. [Connecting to Claude Desktop](#connecting-to-claude-desktop)
6. [Production Deployment](#production-deployment)
7. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required Software

- **Python 3.11+** ([download](https://www.python.org/downloads/))
- **pip** (comes with Python)
- **Git** ([download](https://git-scm.com/downloads))
- **Supabase Account** (free tier available at [supabase.com](https://supabase.com))

### Verify Installation

```bash
python --version  # Should show 3.11 or higher
pip --version
git --version
```

---

## Database Setup (Supabase)

### Step 1: Create Supabase Project

1. Go to [supabase.com](https://supabase.com) and sign in
2. Click **"New Project"**
3. Fill in:
   - **Name**: `expense-mcp` (or your choice)
   - **Database Password**: Save this securely!
   - **Region**: Choose closest to you
4. Click **"Create new project"** (takes ~2 minutes)

### Step 2: Get Connection Details

1. In your Supabase project dashboard, go to **Settings** → **API**
2. Copy these values (you'll need them later):
   - **Project URL**: `https://xxxxx.supabase.co`
   - **anon public key**: Long string starting with `eyJ...`

### Step 3: Create Database Users

1. Go to **Authentication** → **Users**
2. Click **"Add user"** → **"Create new user"**
3. Fill in:
   - **Email**: Your test email (e.g., `rahul@example.com`)
   - **Password**: Choose a password
   - **Auto Confirm User**: ✅ Enable
4. Click **"Create user"**
5. **Repeat** to create more test users (e.g., `priya@example.com`, `amit@example.com`)

### Step 4: Run Database Migrations

1. In Supabase dashboard, go to **SQL Editor**
2. Click **"New query"**
3. Copy and paste the contents of each migration file **in order**:

#### Migration 1: Core Schema (Required)
```sql
-- Copy entire contents of: supabase/migrations/002_collaborative_finance.sql
-- Paste into SQL Editor and click "Run"
```

#### Migration 2: Settlement Recording (Required)
```sql
-- Copy entire contents of: supabase/migrations/005_settlement_recording.sql
-- Paste into SQL Editor and click "Run"
```

#### Migration 3: Performance Optimization (Recommended)
```sql
-- Copy entire contents of: supabase/migrations/004_pending_approvals_optimization.sql
-- Paste into SQL Editor and click "Run"
```

#### Migration 4: Security Hardening (Recommended)
```sql
-- Copy entire contents of: supabase/migrations/006_security_audit.sql
-- Paste into SQL Editor and click "Run"
```

### Step 5: Get User Access Token (JWT)

**Option A: Using Supabase Dashboard (Quick)**

1. Go to **Authentication** → **Users**
2. Click on the user you created (e.g., `rahul@example.com`)
3. Scroll down to **"User UID"** and copy it
4. Go to **SQL Editor** and run:
   ```sql
   SELECT auth.sign_in_with_password('rahul@example.com', 'your-password');
   ```
5. This returns a JWT token - copy the `access_token` value

**Option B: Using API (Programmatic)**

```bash
curl -X POST 'https://YOUR_PROJECT.supabase.co/auth/v1/token?grant_type=password' \
  -H "apikey: YOUR_ANON_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "rahul@example.com",
    "password": "your-password"
  }'
```

Copy the `access_token` from the response.

**💡 Tip for Indian Users**: Choose **Mumbai (ap-south-1)** region when creating your Supabase project for best performance.

**⚠️ Important**: Access tokens expire after 1 hour by default. You'll need to refresh them periodically.

**💡 Tip for Indian Users**: Supabase's free tier works great for personal/small group usage. For production, consider Mumbai region for lower latency.

---

## Local Development

### Step 1: Clone and Install

```bash
# Clone the repository
cd I_M_Expense_MCP

# Create virtual environment (recommended)
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install the package in editable mode
pip install -e .
```

### Step 2: Configure Environment

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` with your values:
   ```env
   SUPABASE_URL=https://xxxxx.supabase.co
   SUPABASE_ANON_KEY=eyJhbGc...your-anon-key
   SUPABASE_ACCESS_TOKEN=eyJhbGc...your-user-jwt-token
   
   # Optional: Server configuration
   MCP_HOST=0.0.0.0
   MCP_PORT=8000
   ```

### Step 3: Start the Server

```bash
# Method 1: Using the installed command
expense-mcp

# Method 2: Using Python module
python -m expense_mcp.server

# You should see:
# INFO:     Started server process
# INFO:     Uvicorn running on http://0.0.0.0:8000
```

---

## Testing the Server

### Test 1: Health Check

```bash
# In a new terminal
curl http://localhost:8000/health
```

Expected response: `{"status": "ok"}`

### Test 2: List Available Tools

```bash
curl http://localhost:8000/tools
```

Should return a JSON list of all available MCP tools.

### Test 3: Call a Tool (whoami)

```bash
curl -X POST http://localhost:8000/call \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "whoami",
    "arguments": {}
  }'
```

Expected response:
```json
{
  "status": "success",
  "user_id": "your-user-uuid"
}
```

### Test 4: Add Personal Expense

```bash
curl -X POST http://localhost:8000/call \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "add_expense",
    "arguments": {
      "date": "2024-01-15",
      "amount": 450,
      "category": "Food & Dining",
      "note": "Lunch at cafe"
    }
  }'
```

### Test 5: Create a Group

```bash
curl -X POST http://localhost:8000/call \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "create_group",
    "arguments": {
      "name": "Weekend Trip",
      "kind": "trip"
    }
  }'
```

---

## Connecting to Claude Desktop

### Step 1: Locate Claude Desktop Config

**Windows**:
```
%APPDATA%\Claude\claude_desktop_config.json
```

**macOS**:
```
~/Library/Application Support/Claude/claude_desktop_config.json
```

**Linux**:
```
~/.config/Claude/claude_desktop_config.json
```

### Step 2: Configure MCP Server

Edit `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "expense-mcp": {
      "command": "python",
      "args": [
        "-m",
        "expense_mcp.server"
      ],
      "env": {
        "SUPABASE_URL": "https://xxxxx.supabase.co",
        "SUPABASE_ANON_KEY": "eyJhbGc...your-anon-key",
        "SUPABASE_ACCESS_TOKEN": "eyJhbGc...your-user-jwt-token",
        "MCP_HOST": "127.0.0.1",
        "MCP_PORT": "8000"
      }
    }
  }
}
```

**⚠️ Important**: Use the **full path** to Python if you're using a virtual environment:

**Windows**:
```json
"command": "C:\\Users\\YourName\\I_M_Expense_MCP\\venv\\Scripts\\python.exe"
```

**macOS/Linux**:
```json
"command": "/Users/yourname/I_M_Expense_MCP/venv/bin/python"
```

### Step 3: Restart Claude Desktop

1. Quit Claude Desktop completely
2. Reopen Claude Desktop
3. Look for the 🔌 icon in the bottom-right corner
4. Click it to see available MCP servers
5. You should see **"expense-mcp"** listed

### Step 4: Test in Claude

Try these prompts:

```
Who am I? (should call whoami tool)

Add an expense: ₹250 for coffee on January 15th

Create a group called "Family Budget"

Show me my expenses from January 1 to January 31

Add a group expense: ₹1500 for dinner, split equally
```

---

## Production Deployment

### Option 1: Deploy as HTTP Server (Recommended)

#### Using Railway.app

1. Go to [railway.app](https://railway.app) and sign in
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Select your forked repository
4. Add environment variables:
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
   - `SUPABASE_ACCESS_TOKEN`
   - `MCP_HOST=0.0.0.0`
   - `MCP_PORT=8000`
5. Railway will auto-deploy on push

#### Using Render.com

1. Go to [render.com](https://render.com) and sign in
2. Click **"New"** → **"Web Service"**
3. Connect your GitHub repository
4. Configure:
   - **Build Command**: `pip install -e .`
   - **Start Command**: `python -m expense_mcp.server`
   - **Environment Variables**: Add your Supabase credentials
5. Click **"Create Web Service"**

### Option 2: Deploy as Serverless Function

#### Using Vercel

1. Install Vercel CLI:
   ```bash
   npm install -g vercel
   ```

2. Create `vercel.json`:
   ```json
   {
     "builds": [
       {
         "src": "src/expense_mcp/server.py",
         "use": "@vercel/python"
       }
     ],
     "routes": [
       {
         "src": "/(.*)",
         "dest": "src/expense_mcp/server.py"
       }
     ]
   }
   ```

3. Deploy:
   ```bash
   vercel --prod
   ```

### Option 3: Self-Hosted (Docker)

1. Create `Dockerfile`:
   ```dockerfile
   FROM python:3.11-slim
   
   WORKDIR /app
   
   COPY pyproject.toml .
   COPY src/ src/
   
   RUN pip install -e .
   
   EXPOSE 8000
   
   CMD ["python", "-m", "expense_mcp.server"]
   ```

2. Build and run:
   ```bash
   docker build -t expense-mcp .
   docker run -p 8000:8000 \
     -e SUPABASE_URL=https://xxxxx.supabase.co \
     -e SUPABASE_ANON_KEY=your-key \
     -e SUPABASE_ACCESS_TOKEN=your-token \
     expense-mcp
   ```

### Update Claude Desktop for Production

```json
{
  "mcpServers": {
    "expense-mcp": {
      "url": "https://your-deployed-server.com",
      "headers": {
        "Authorization": "Bearer your-api-key"
      }
    }
  }
}
```

---

## Troubleshooting

### Issue: "Module not found: expense_mcp"

**Solution**:
```bash
# Make sure you're in the project directory
cd I_M_Expense_MCP

# Reinstall in editable mode
pip install -e .
```

### Issue: "SUPABASE_ACCESS_TOKEN not set"

**Solution**:
1. Check your `.env` file exists
2. Verify the token is not expired (tokens expire after 1 hour)
3. Get a fresh token using the steps in [Database Setup](#step-5-get-user-access-token-jwt)

### Issue: "RLS policy violation" or "Permission denied"

**Solution**:
1. Verify migrations ran successfully in Supabase SQL Editor
2. Check that your access token belongs to a valid user
3. Verify the user is a member of the group you're trying to access

### Issue: Claude Desktop doesn't show the MCP server

**Solution**:
1. Check `claude_desktop_config.json` syntax (use a JSON validator)
2. Use **absolute paths** for the Python command
3. Check Claude Desktop logs:
   - **Windows**: `%APPDATA%\Claude\logs`
   - **macOS**: `~/Library/Logs/Claude`
   - **Linux**: `~/.config/Claude/logs`

### Issue: "Connection refused" when testing

**Solution**:
```bash
# Check if server is running
curl http://localhost:8000/health

# Check if port is in use
# Windows:
netstat -ano | findstr :8000
# macOS/Linux:
lsof -i :8000

# Try a different port
export MCP_PORT=8001
python -m expense_mcp.server
```

### Issue: Token expires too quickly

**Solution**: Implement token refresh logic

Create `refresh_token.py`:
```python
import os
from supabase import create_client

url = os.environ["SUPABASE_URL"]
key = os.environ["SUPABASE_ANON_KEY"]
client = create_client(url, key)

# Sign in to get new token
response = client.auth.sign_in_with_password({
    "email": "rahul@example.com",
    "password": "your-password"
})

print(f"New access token: {response.session.access_token}")
print(f"Expires at: {response.session.expires_at}")
```

Run periodically:
```bash
python refresh_token.py
# Update .env with new token
```

**💡 Tip**: Set up a cron job to refresh tokens automatically every 50 minutes.

### Issue: Materialized view not refreshing

**Solution**:
```sql
-- Manually refresh in Supabase SQL Editor
REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_user_pending_approvals;

-- Check if triggers are enabled
SELECT tgname, tgenabled FROM pg_trigger 
WHERE tgrelid = 'public.transactions'::regclass;
```

---

## Next Steps

1. **Read the Architecture**: See [ARCHITECTURE.md](./ARCHITECTURE.md) for design decisions
2. **Explore Tools**: Try all 20+ MCP tools via Claude
3. **Multi-User Testing**: Create multiple users and test group workflows
4. **Custom Categories**: Create `categories.json` and set `EXPENSE_CATEGORIES_PATH`
5. **Monitor Performance**: Check Supabase dashboard for query performance

---

## Quick Reference

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SUPABASE_URL` | ✅ Yes | Your Supabase project URL |
| `SUPABASE_ANON_KEY` | ✅ Yes | Supabase anon public key |
| `SUPABASE_ACCESS_TOKEN` | ✅ Yes | User JWT (refresh hourly) |
| `MCP_HOST` | ❌ No | Server bind address (default: 0.0.0.0) |
| `MCP_PORT` | ❌ No | Server port (default: 8000) |
| `EXPENSE_CATEGORIES_PATH` | ❌ No | Custom categories JSON file |

### Common Commands

```bash
# Start server
python -m expense_mcp.server

# Install/update dependencies
pip install -e .

# Test health
curl http://localhost:8000/health

# View logs (if using systemd)
journalctl -u expense-mcp -f
```

### Useful SQL Queries

```sql
-- Check RLS is enabled
SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public';

-- View all groups
SELECT * FROM public.groups;

-- View pending approvals
SELECT * FROM public.mv_user_pending_approvals;

-- Check user's groups
SELECT g.* FROM public.groups g
JOIN public.group_members gm ON gm.group_id = g.id
WHERE gm.user_id = 'your-user-uuid';
```

---

## Support

- **Issues**: [GitHub Issues](https://github.com/yourusername/I_M_Expense_MCP/issues)
- **Supabase Docs**: [supabase.com/docs](https://supabase.com/docs)
- **FastMCP Docs**: [github.com/jlowin/fastmcp](https://github.com/jlowin/fastmcp)
