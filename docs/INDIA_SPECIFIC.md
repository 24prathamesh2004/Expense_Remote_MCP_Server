# India-Specific Features & Customizations

This document highlights features and configurations specifically tailored for Indian users.

---

## 🇮🇳 Currency: Indian Rupees (INR)

### Default Currency
All amounts are stored and displayed in **Indian Rupees (₹)**.

```python
# Database default
currency text not null default 'INR'
```

### Paisa-Level Precision
Equal splits are calculated at **paisa level** (1/100th of a rupee) to ensure exact amounts.

**Example**:
```
₹1000 split among 3 members:
- Member 1: ₹333.33
- Member 2: ₹333.33
- Member 3: ₹333.34 (gets the extra paisa)
```

---

## 💳 Payment Methods

### Supported Indian Payment Systems

The `note` field in `record_settlement()` supports common Indian payment methods:

- **UPI** (Unified Payments Interface)
- **PhonePe**
- **Google Pay (GPay)**
- **Paytm**
- **BHIM**
- **Bank Transfer (NEFT/RTGS/IMPS)**
- **Cash**

**Example Usage**:
```python
record_settlement(
    group_id="...",
    from_user_id="rahul",
    to_user_id="priya",
    amount=500,
    note="PhonePe payment"
)
```

---

## 📊 Indian Expense Categories

### Default Categories (19 categories)

The system includes India-specific categories:

1. **Food & Dining**
   - Restaurants, Street Food, Cafes & Tea, Sweets & Snacks
   - Food Delivery, Tiffin Service

2. **Groceries**
   - Vegetables & Fruits, Dairy & Eggs, Staples & Grains
   - Packaged Foods, Household Items

3. **Transportation**
   - Auto/Rickshaw, Taxi/Ola/Uber
   - Metro/Local Train, Bus, Parking, Toll

4. **Fuel & Vehicle**
   - Petrol/Diesel, CNG
   - Vehicle Maintenance, Vehicle Insurance
   - Two-Wheeler Service, Car Service

5. **Mobile & Internet**
   - Mobile Recharge, Broadband/WiFi
   - DTH Recharge, OTT Subscriptions

6. **Bills & Utilities**
   - Electricity, Water, Gas/LPG
   - Maintenance Charges, Property Tax, DTH/Cable TV

7. **Rent**
   - House Rent, PG/Hostel
   - Security Deposit, Brokerage

8. **EMI & Loans**
   - Home Loan EMI, Car Loan EMI
   - Personal Loan EMI, Credit Card Payment
   - Education Loan EMI

9. **Investments**
   - Mutual Funds, Fixed Deposits, PPF/EPF
   - Stocks, Gold, Insurance Premium

10. **Donations**
    - Charity, Religious
    - NGO Contributions, Temple/Church/Mosque

11. **Healthcare**
    - Doctor Consultation, Medicines, Lab Tests
    - Hospital Bills, Health Insurance, Dental Care

12. **Education**
    - School Fees, Tuition Classes
    - Books & Supplies, Online Courses
    - Exam Fees, Coaching

13. **Travel**
    - Flight Tickets, Train Tickets, Bus Tickets
    - Hotel/Accommodation, Travel Insurance
    - Visa & Documents

14. **Shopping**
    - Clothing, Footwear, Electronics
    - Books & Stationery, Gifts, Online Shopping

15. **Entertainment**
    - Movies, Events & Concerts
    - Streaming Services, Gaming
    - Sports & Fitness, Hobbies

16. **Personal Care**
    - Salon/Barber, Cosmetics
    - Spa & Wellness, Gym Membership

17. **Household**
    - Furniture, Appliances, Home Decor
    - Repairs & Maintenance, Cleaning Supplies
    - Maid/Cook Salary

18. **Business**
    - Office Supplies, Business Travel
    - Client Meetings, Software/Tools
    - Professional Services

19. **Other**
    - Miscellaneous, Emergency, Uncategorized

### Custom Categories

To use custom categories, create `categories.json` in the project root:

```json
{
  "categories": [
    {
      "name": "Food & Dining",
      "subcategories": [
        "Restaurants",
        "Street Food",
        "Tiffin Service"
      ]
    }
  ]
}
```

Set environment variable:
```bash
EXPENSE_CATEGORIES_PATH=/path/to/categories.json
```

---

## 🌏 Supabase Region Recommendation

### Mumbai Region (ap-south-1)

For best performance in India, choose **Mumbai** region when creating your Supabase project:

**Benefits**:
- **Lower latency**: ~10-50ms vs 200-300ms for Singapore/US regions
- **Better reliability**: Closer to Indian ISPs
- **Compliance**: Data stays in India

**How to Select**:
1. When creating Supabase project
2. Choose **Region**: `South Asia (Mumbai)`
3. This sets up your database in AWS ap-south-1

---

## 💡 Common Indian Use Cases

### 1. Family Expenses
```
Group: "Family Budget"
Members: Parents, Children
Categories: Groceries, Bills, Education, Healthcare
Settlement: Monthly via UPI
```

### 2. Roommate Expenses
```
Group: "Flat 301"
Members: 3-4 roommates
Categories: Rent, Electricity, Groceries, Maid Salary
Settlement: Monthly split via PhonePe/GPay
```

### 3. Trip Expenses
```
Group: "Goa Trip 2024"
Members: Friends group
Categories: Travel, Food, Accommodation, Entertainment
Settlement: After trip via UPI
```

### 4. Office Team Lunch
```
Group: "Team Outings"
Members: Office colleagues
Categories: Food & Dining
Settlement: Immediate via GPay
```

### 5. Wedding/Event Planning
```
Group: "Rahul's Wedding"
Members: Family members
Categories: Shopping, Food, Decorations, Travel
Settlement: Post-event via bank transfer
```

---

## 📱 Indian Mobile Integration Ideas

### Future Enhancements

**UPI Deep Links**:
```python
# Generate UPI payment link
upi_link = f"upi://pay?pa={receiver_upi}&pn={name}&am={amount}&cu=INR"
```

**WhatsApp Integration**:
- Send expense notifications via WhatsApp Business API
- Share settlement summaries in group chats

**SMS Notifications**:
- Send OTP for expense approval
- SMS reminders for pending approvals

**Receipt OCR**:
- Parse Indian restaurant bills
- Extract GST details
- Auto-categorize based on merchant name

---

## 🏦 Indian Banking Features

### Potential Integrations

**Bank Statement Import**:
- Parse PDF/Excel bank statements
- Auto-categorize transactions
- Match with recorded expenses

**Credit Card Integration**:
- Import credit card statements
- Track EMI payments
- Categorize merchant transactions

**UPI Transaction History**:
- Import from PhonePe/GPay/Paytm
- Auto-match with group expenses
- Verify settlement payments

---

## 📊 Indian Tax & Compliance

### GST Tracking (Future)

```sql
-- Add GST fields to transactions
alter table transactions add column gst_amount numeric(14,2);
alter table transactions add column gst_number text;
alter table transactions add column hsn_code text;
```

### Income Tax Reporting

Generate reports for:
- Medical expenses (Section 80D)
- Education expenses (Section 80E)
- Home loan interest (Section 24)
- Donations (Section 80G)

---

## 🎯 Best Practices for Indian Users

### 1. Token Refresh Strategy
```bash
# Set up cron job (every 50 minutes)
*/50 * * * * /path/to/refresh_token.sh
```

### 2. Backup Strategy
```bash
# Daily backup of Supabase database
pg_dump -h db.xxx.supabase.co -U postgres -d postgres > backup_$(date +%Y%m%d).sql
```

### 3. Category Naming
Use Hindi/regional language names if preferred:
```json
{
  "categories": [
    "खाना और भोजन (Food & Dining)",
    "यात्रा (Travel)",
    "किराना (Groceries)"
  ]
}
```

### 4. Settlement Timing
- **Monthly**: For recurring expenses (rent, bills)
- **Weekly**: For shared groceries
- **Immediate**: For one-time events (dinners, trips)

### 5. Group Naming Conventions
```
Format: [Type] - [Name] - [Year]
Examples:
- Family - Monthly Budget - 2024
- Trip - Goa - Jan2024
- Flat - 301 - 2024
```

---

## 🔐 Security for Indian Users

### Data Privacy
- All data stored in Mumbai region (India)
- RLS ensures user isolation
- No data shared with third parties

### Compliance
- Follows Indian data protection guidelines
- Audit logs for all transactions
- User data deletion on request

### Best Practices
- Use strong passwords (min 12 characters)
- Enable 2FA on Supabase account
- Rotate access tokens regularly
- Don't share JWT tokens via WhatsApp/SMS

---

## 📞 Support & Community

### Indian User Groups
- **Telegram**: (Create a group for Indian users)
- **WhatsApp**: (Community for feature requests)
- **Discord**: (Technical support channel)

### Regional Language Support (Future)
- Hindi UI translations
- Regional language categories
- Voice input in Indian languages

---

## 🚀 Quick Start for Indian Users

```bash
# 1. Create Supabase project (Mumbai region)
# 2. Run migrations
# 3. Create test users (e.g., rahul@example.com, priya@example.com)

# 4. Install
pip install -e .

# 5. Configure
cp .env.example .env
# Add your Supabase credentials

# 6. Start
python -m expense_mcp.server

# 7. Test with Indian scenarios
curl -X POST http://localhost:8000/call \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "add_expense",
    "arguments": {
      "date": "2024-01-15",
      "amount": 450,
      "category": "Food & Dining",
      "subcategory": "Street Food",
      "note": "Pani puri at Juhu Beach"
    }
  }'
```

---

## 💰 Pricing for Indian Users

### Supabase Free Tier
- **Database**: 500 MB (sufficient for 10,000+ transactions)
- **API Requests**: 50,000/month
- **Bandwidth**: 2 GB/month
- **Cost**: ₹0/month

### Supabase Pro Tier
- **Database**: 8 GB
- **API Requests**: 500,000/month
- **Bandwidth**: 50 GB/month
- **Cost**: ~₹2,000/month ($25/month)

### Recommended for Indian Users
- **Personal/Family**: Free tier
- **Small Groups (<10 members)**: Free tier
- **Large Groups/Business**: Pro tier

---

## 🎉 Success Stories (Examples)

### Case Study 1: Mumbai Family
- **Members**: 4 (parents + 2 children)
- **Monthly Expenses**: ₹50,000
- **Categories**: Groceries, Bills, Education
- **Settlement**: Monthly via UPI
- **Result**: 30% better expense tracking

### Case Study 2: Bangalore Roommates
- **Members**: 3 roommates
- **Monthly Expenses**: ₹30,000
- **Categories**: Rent, Electricity, Groceries, Maid
- **Settlement**: Monthly split via PhonePe
- **Result**: Zero disputes, transparent tracking

### Case Study 3: Delhi Office Team
- **Members**: 8 colleagues
- **Monthly Expenses**: ₹15,000 (team lunches)
- **Categories**: Food & Dining
- **Settlement**: Immediate via GPay
- **Result**: Simplified team expense management

---

## 📚 Additional Resources

- **Indian Banking Codes**: [IFSC Code Finder](https://www.rbi.org.in)
- **UPI Documentation**: [NPCI UPI](https://www.npci.org.in/what-we-do/upi)
- **GST Portal**: [GST India](https://www.gst.gov.in)
- **Income Tax**: [IT Department](https://www.incometax.gov.in)
