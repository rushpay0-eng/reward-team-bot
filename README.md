# Reward Bot Complete Pro

Professional Telegram reward bot with:

- Dynamic single-dashboard navigation
- Back to Dashboard on every user screen
- Real Scratch Cards and Lucky Wheel
- Registration and Newbie proof review
- Secure proof image preview in Admin Panel
- 24-hour admin analytics and activity log
- Complete withdrawal history
- Admin-controlled reward settings
- Daily Check-in bonus
- Atomic duplicate-claim protection
- Railway Volume-ready database

## Required GitHub structure

main.py
requirements.txt
Procfile
.env.example
.gitignore
README.md

templates/
  scratch.html
  wheel.html
  login.html
  admin.html

static/
  miniapp.css
  admin.css

## Railway

Start command:

python main.py

Public Network port:

8080

Attach a Volume at:

/data

Admin Panel:

https://YOUR-DOMAIN.up.railway.app/admin


## 3-Level Referral System

Railway variable required:

BOT_USERNAME=YourBotUsername

Default commission rates:

- Level 1: 0.3%
- Level 2: 0.2%
- Level 3: 0.1%

Referral commission applies to First Scratch, Second Scratch, Lucky Wheel and Daily Check-in rewards.


## Final Polished Patch

- Dashboard displays Task Balance, Referral Balance, Total Referral Earnings and Total Available.
- Community button text is `Join Community`.
- The first Scratch Card performs a live Channel and Group membership check when opened and claimed.
- Telegram controls message-bubble width, but the wider separator layout improves presentation.


## Final Admin Update
- Instant refreshed dashboard after Scratch and Wheel claim.
- Detailed Level 1/2/3 referral activity.
- Separate Registration, Newbie and Withdrawal sections.
- Compact dashboard metrics and central Update Amounts section.


## Registration Post and Reading Timer

The Admin Panel now includes a separate `Registration Post` section.

Admin can control:

- Post title and subtitle
- Long registration content up to 50,000 characters
- Reading timer from 0 to 3,600 seconds
- Required full-page scroll
- Required “I have read and understood” confirmation
- Continue button text
- Registration target link
- Publish/unpublish switch

The content is stored in the existing persistent SQLite database. No new Railway variable is required. If the Admin Panel registration link is blank, the existing `REGISTRATION_LINK` Railway variable is used.
