# Quick Start for Users (2 Minutes)

## ⏱️ One-Time Setup (Do This Once)

### Step 1: Add Server to Claude Desktop

**Windows**: Edit `%APPDATA%\Claude\claude_desktop_config.json`
**macOS**: Edit `~/Library/Application Support/Claude/claude_desktop_config.json`

Add this:
```json
{
  "mcpServers": {
    "expense-mcp": {
      "url": "https://your-expense-server.railway.app"
    }
  }
}
```

Save and **restart Claude Desktop**.

---

### Step 2: Register Your Account

Open Claude Desktop and say:

```
Register me for the expense tracker:
- Email: your-email@example.com
- Password: YourSecurePassword123
- Name: Your Full Name
```

Claude will respond with something like:

```
✅ Registration successful!

Your API Key: exp_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6

⚠️ IMPORTANT: Save this API key! You'll need it in the next step.
It will not be shown again.
```

**Copy the API key** (starts with `exp_`)

---

### Step 3: Add Your API Key to Config

Edit `claude_desktop_config.json` again:

```json
{
  "mcpServers": {
    "expense-mcp": {
      "url": "https://your-expense-server.railway.app",
      "headers": {
        "X-API-Key": "exp_YOUR_KEY_HERE"
      }
    }
  }
}
```

Replace `exp_YOUR_KEY_HERE` with the API key from Step 2.

Save and **restart Claude Desktop** one more time.

---

### Step 4: Test It!

Say to Claude:

```
Add an expense: ₹500 for lunch today
```

If it works, you're all set! 🎉

---

## ✅ You're Done Forever!

**That's it!** You never need to register or login again.

Every time you open Claude Desktop from now on:
- Your API key is automatically included
- You're automatically authenticated
- Just talk naturally about expenses

---

## 💬 What You Can Do Now

### Personal Expenses
```
Add an expense: ₹250 for coffee on January 15th

Show me my expenses from January 1 to January 31

Summarize my spending by category this month
```

### Groups
```
Create a group called "Family Budget"

Invite someone to my group

Add a group expense: ₹1500 for dinner, split equally

Show me pending approvals

Approve the dinner expense

Show group balances

Suggest settlements for the group
```

### Settlements
```
Record a payment: I paid Priya ₹500 via PhonePe

Show settlement history for the group
```

---

## 🔧 Troubleshooting

### "API key required" error

**Problem**: Claude can't find your API key.

**Solution**:
1. Check `claude_desktop_config.json` has the `X-API-Key` header
2. Make sure there are no typos in the API key
3. Restart Claude Desktop

---

### "Invalid or expired API key" error

**Problem**: Your API key was revoked or is incorrect.

**Solution**: Get a new API key by saying:

```
Login and get my API key:
- Email: your-email@example.com
- Password: YourPassword123
```

Then update your config with the new key.

---

### "User already exists" error

**Problem**: You're trying to register but already have an account.

**Solution**: Use the login tool instead:

```
Login and get my API key:
- Email: your-email@example.com
- Password: YourPassword123
```

---

### Lost your API key?

**Solution**: Say to Claude:

```
I lost my API key. Can you help me get a new one?

Email: your-email@example.com
Password: YourPassword123
```

Claude will give you a new API key. Update your config and restart.

---

### Forgot your password?

**Solution**: You'll need to register a new account with a different email, or contact the server admin to reset your password.

---

## 🔐 Security Tips

### ✅ Do:
- Keep your API key private (like a password)
- Save your API key in a password manager
- Use a strong password (min 8 characters)

### ❌ Don't:
- Share your API key with others
- Commit your config file to git
- Use the same password as other accounts

### If Your API Key Is Compromised:

Say to Claude:
```
Revoke my API key: exp_YOUR_OLD_KEY
```

Then login to get a new one:
```
Login and get my API key:
- Email: your-email@example.com
- Password: YourPassword123
```

---

## 📱 Using on Multiple Devices

If you want to use the expense tracker on multiple computers:

1. **Option A**: Use the same API key on all devices
   - Copy your API key from the first computer
   - Add it to Claude config on other computers

2. **Option B**: Get a separate API key for each device
   - Login on each device to get a unique key
   - Easier to revoke if one device is lost

---

## 🎓 Learning More

### Categories

The system includes India-specific categories:
- Food & Dining (Street Food, Tiffin Service)
- Transportation (Auto/Rickshaw, Metro)
- Fuel & Vehicle (Petrol/Diesel, CNG)
- Mobile & Internet (Recharge, DTH)
- And 15 more...

### Payment Methods

When recording settlements, mention the payment method:
- "Paid via UPI"
- "Paid via PhonePe"
- "Paid via GPay"
- "Paid via Paytm"
- "Paid in cash"

### Group Workflows

1. **Create group** → Get group ID
2. **Invite members** → Share invite code
3. **Add expenses** → Automatically split equally
4. **Approve expenses** → All members must approve
5. **Check balances** → See who owes whom
6. **Settle up** → Record real payments

---

## 💡 Pro Tips

### Tip 1: Natural Language
You don't need exact commands. Just talk naturally:
```
"I spent 500 rupees on groceries yesterday"
"Show me what I spent on food this month"
"Split the dinner bill with my roommates"
```

### Tip 2: Date Formats
You can use natural dates:
```
"today"
"yesterday"
"January 15"
"last Monday"
"2024-01-15"
```

### Tip 3: Categories
If you're not sure about categories, just describe the expense:
```
"I bought vegetables for 200 rupees"
Claude will suggest: Category = "Groceries"
```

### Tip 4: Group Names
Use descriptive group names:
```
✅ "Goa Trip Jan 2024"
✅ "Flat 301 Roommates"
✅ "Family Monthly Budget"

❌ "Group 1"
❌ "Test"
```

---

## 🆘 Need Help?

If you're stuck:

1. **Check this guide** - Most issues are covered above
2. **Ask Claude** - "How do I add a group expense?"
3. **Contact server admin** - If server is down or you need password reset

---

## 🎉 Welcome!

You're now part of the expense tracking system. Happy tracking! 💰

**Remember**: You only did this setup once. From now on, just open Claude and start tracking expenses naturally!
