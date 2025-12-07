# 🚀 Saarthi — Crypto Futures Trading Platform v2.0

**Advanced crypto trading terminal with authentication, risk management, and live CoinDCX integration**
---

## ✨ Features

### 🔐 Authentication & Security
- ✅ JWT-based user authentication
- ✅ Secure password hashing (bcrypt)
- ✅ User registration and login
- ✅ Role-based access control (Admin, User, Demo)
- ✅ Session management with token refresh
- ✅ Protected API endpoints

### 📊 Trading Features
- ✅ Real-time price streaming via WebSocket
- ✅ Multi-order queue management
- ✅ Futures trading with 1-100x leverage
- ✅ Risk-based position sizing (Risk * 100 / SL)
- ✅ Live INR margin tracking
- ✅ Market & limit order support
- ✅ Bulk order execution
- ✅ Order history and tracking

### 🗄️ Data Management
- ✅ SQLite (development) / PostgreSQL (production)
- ✅ Persistent order storage
- ✅ User-specific data isolation
- ✅ Trading history and analytics
- ✅ Favorite symbols management
- ✅ Audit logging

### 🛡️ Risk Management
- ✅ Per-user leverage limits
- ✅ Position size limits
- ✅ Daily loss limits
- ✅ Customizable risk per trade
- ✅ Real-time margin calculations

### 🎨 User Experience
- ✅ Modern, responsive UI
- ✅ Light mode design
- ✅ Real-time updates
- ✅ Mobile-friendly interface
- ✅ Interactive order management

---

## 🏗️ Architecture

```
┌─────────────────┐
│  Frontend (JS)  │
│   - Login UI    │
│   - Trading UI  │
└────────┬────────┘
         │ HTTP/WS + JWT
         ▼
┌─────────────────┐
│   FastAPI       │
│   - Auth Routes │
│   - Trade Routes│
│   - WebSocket   │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐ ┌──────────┐
│Database│ │ CoinDCX  │
│SQLAlchemy│ Futures API│
└────────┘ └──────────┘
```

---
## 🗂️ Project Structure

```
saarthi/
├── backend/
│   ├── main.py                 # FastAPI application
│   ├── database.py             # Database configuration
│   ├── requirements.txt        # Python dependencies
│   ├── models/
│   │   └── database.py         # SQLAlchemy models
│   ├── routes/
│   │   ├── auth.py             # Authentication endpoints
│   │   ├── trading.py          # Trading endpoints
│   │   └── user_orders.py      # Order management
│   ├── utils/
│   │   └── auth.py             # Auth utilities
│   └── services/
│       ├── exchange.py         # Exchange integration
│       └── price_broadcaster.py # WebSocket price streaming
├── frontend/
│   ├── index.html              # Trading dashboard
│   └── login.html              # Login/Register page
├── .env.example                # Environment template
├── .gitignore                  # Git ignore rules
├── Dockerfile                  # Docker configuration
├── setup.sh                    # Setup script (Linux/Mac)
├── setup.bat                   # Setup script (Windows)
├── README.md                   # Original README
├── README_V2.md                # This file
├── UPGRADE_SUMMARY.md          # Detailed changes
└── DEPLOYMENT.md               # Deployment guide
```

---

## 🔌 API Endpoints

### Authentication (`/api/auth`)

| Method | Endpoint | Description | Auth Required |
|--------|----------|-------------|---------------|
| POST | `/register` | Create new account | ❌ |
| POST | `/login` | Login with credentials | ❌ |
| POST | `/refresh` | Refresh access token | ❌ |
| GET | `/me` | Get current user | ✅ |
| PUT | `/me` | Update user profile | ✅ |
| POST | `/change-password` | Change password | ✅ |

### Trading (`/api/trade`)

| Method | Endpoint | Description | Auth Required |
|--------|----------|-------------|---------------|
| GET | `/securities` | List available symbols | ✅ |
| GET | `/price/{symbol}` | Get symbol price | ✅ |
| GET | `/balance` | Get account balance | ✅ |
| WS | `/ws/price` | Real-time price stream | ✅ |

### Orders (`/api/orders`)

| Method | Endpoint | Description | Auth Required |
|--------|----------|-------------|---------------|
| POST | `/` | Create order | ✅ |
| POST | `/bulk` | Create multiple orders | ✅ |
| GET | `/` | List user's orders | ✅ |
| GET | `/queued` | Get queued orders | ✅ |
| GET | `/{id}` | Get specific order | ✅ |
| PUT | `/{id}` | Update order | ✅ |
| DELETE | `/{id}` | Delete order | ✅ |
| DELETE | `/` | Clear queue | ✅ |
| GET | `/history/executed` | Execution history | ✅ |


### For Development
- ✅ Use strong SECRET_KEY (32+ characters)
- ✅ Keep .env file private (never commit)
- ✅ Use HTTPS in production
- ✅ Rotate API keys regularly

### For Production
- ✅ Use PostgreSQL (not SQLite)
- ✅ Enable HTTPS/SSL
- ✅ Use environment secrets (not .env)
- ✅ Set CORS properly
- ✅ Enable rate limiting
- ✅ Use strong passwords
- ✅ Regular backups
- ✅ Monitor logs

---

## 🆚 Version Comparison

| Feature | v1.0 | v2.0 |
|---------|------|------|
| Authentication | ❌ | ✅ JWT |
| Multi-user | ❌ | ✅ Full support |
| Database | ❌ In-memory | ✅ SQLAlchemy |
| Order persistence | ❌ | ✅ |
| User roles | ❌ | ✅ Admin/User/Demo |
| Risk management | ⚠️ Basic | ✅ Advanced |
| Audit logs | ❌ | ✅ |
| API protection | ❌ | ✅ JWT auth |
| Production ready | ⚠️ | ✅ |

---

## 📈 Roadmap

### v2.1 (Planned)
- [ ] Email verification
- [ ] Two-factor authentication (2FA)
- [ ] Password reset via email
- [ ] Paper trading mode

### v2.2 (Planned)
- [ ] Advanced analytics dashboard
- [ ] P&L tracking
- [ ] Portfolio management
- [ ] Trade journaling

### v3.0 (Future)
- [ ] Mobile app (React Native)
- [ ] Advanced charting
- [ ] Strategy builder
- [ ] Social trading features

---

## 🤝 Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## ⚠️ Disclaimer

**IMPORTANT:** This is educational software for learning purposes. Trading cryptocurrencies involves substantial risk of loss. The developers are not responsible for any financial losses incurred through the use of this software.

- Use at your own risk
- Test thoroughly before live trading
- Start with small amounts
- Never invest more than you can afford to lose

---

## 🎯 Quick Links

- 🏠 [Homepage](http://localhost:8000/)
- 🔐 [Login](http://localhost:8000/login.html)
- 📊 [Trading Platform](http://localhost:8000/index.html)
- 📚 [API Docs](http://localhost:8000/api/docs)
- 💚 [Health Check](http://localhost:8000/health)

---

**Made with ❤️ for traders**

**Version:** 2.0.0  
**Last Updated:** December 2024
