# Mandatory Channel Join System - Implementation Guide

## Overview
A fully dynamic, production-ready Telegram bot feature that enforces mandatory channel membership. Admins can manage channels through an admin panel with FSM-based form handling.

## Architecture

### 1. Database Layer (`app/models.py`)
```python
class MandatoryChannel(TimestampMixin, Base):
    - id: Primary key
    - channel_id: Telegram channel ID (BIGINT, unique)
    - channel_name: Display name (String 255)
    - invite_link: Channel invite URL (Text)
    - is_active: Active/inactive toggle (Boolean)
    - created_at, updated_at: Timestamps
```

### 2. Repository (`app/repositories/mandatory_channels.py`)
Methods:
- `get_all_active()`: Fetch all active channels
- `get_by_channel_id()`: Get channel by Telegram ID
- `create()`: Add new channel
- `delete_by_id()`: Delete by database ID
- `delete_by_channel_id()`: Delete by Telegram ID
- `exists()`: Check if channel exists

### 3. Middleware (`bot/middlewares/mandatory_channels.py`)
**DynamicMandatoryJoinMiddleware**
- Checks all private chat messages
- Verifies user membership in all active mandatory channels
- If user hasn't joined all channels:
  - Sends message: "❌ برای استفاده از ربات، باید در کانال‌های زیر عضو شوید:"
  - Shows button for each unjoined channel with invite link
  - Adds "🔄 بررسی مجدد" (Refresh Check) button
  - Blocks handler chain (prevents command execution)
- Handles bot API errors gracefully with logging

### 4. Admin Router (`bot/routers/mandatory_channels.py`)

#### Commands:
- `/admin_channels`: List all channels with delete/add buttons

#### FSM States:
```
MandatoryChannelCreationStates:
  - waiting_for_channel_id: Input channel ID
  - waiting_for_channel_name: Input display name
  - waiting_for_invite_link: Input invite URL
```

#### Handlers:
1. **cmd_admin_channels**: Display channel list
   - Shows all active channels with delete buttons
   - "➕ افزودن کانال جدید" (Add New) button
   - Admin-only access via `is_admin_identity()` filter

2. **callback_add_new_channel**: Start creation workflow
   - Sets FSM to `waiting_for_channel_id`

3. **process_channel_id**: Validate and store channel ID
   - Validates integer format
   - Checks for duplicates
   - Moves to `waiting_for_channel_name`

4. **process_channel_name**: Validate and store display name
   - Max 255 characters
   - Moves to `waiting_for_invite_link`

5. **process_invite_link**: Save to database
   - Validates URL format
   - Converts `t.me/...` to `https://t.me/...`
   - Saves to database
   - Clears FSM state

6. **callback_delete_channel**: Delete channel by ID
   - Admin-only verification
   - Removes from database
   - Refreshes message

7. **callback_mandatory_join_check**: Refresh check
   - Re-verifies user membership
   - If all channels joined: deletes message + success notification
   - If still missing: updates button list

8. **cmd_cancel_channel_creation**: Cancel FSM workflow
   - Clears state and confirms cancellation

## Integration in `bot/loader.py`

```python
# 1. Import middleware and router
from bot.middlewares.mandatory_channels import DynamicMandatoryJoinMiddleware
from bot.routers import ... mandatory_channels ...

# 2. In create_dispatcher():
db_middleware = DbSessionMiddleware(async_session_maker)
dp.update.middleware(db_middleware)

mandatory_join_middleware = DynamicMandatoryJoinMiddleware()
dp.update.middleware(mandatory_join_middleware)

# 3. Include router (before admin router):
dp.include_router(mandatory_channels.router)
```

## Database Migration

Run Alembic migration:
```bash
alembic upgrade head
```

Creates table with unique index on `channel_id`.

## Admin Usage

### Add Channel:
```
/admin_channels
→ Click "➕ افزودن کانال جدید"
→ Enter channel ID: -1001234567890
→ Enter display name: کانال اصلی
→ Enter invite link: https://t.me/mychannel
→ ✅ Saved
```

### Delete Channel:
```
/admin_channels
→ Click "❌ کانال نام"
→ ✅ Deleted
```

### User Flow:
1. User sends command in private chat
2. Middleware checks mandatory channels
3. If missing: shows join buttons + refresh button
4. User joins channels
5. Clicks refresh → re-checks → grants access

## Error Handling
- Bot removed from channel: Treats as unjoined (safe default)
- API errors: Logged via structlog, gracefully degraded
- Missing database session: Skips check
- Invalid channel ID: User-friendly error message
- Duplicate channel: Prevents re-adding

## Performance Considerations
- Channels fetched fresh each message (can be cached if needed)
- Member checks parallelizable with `asyncio.gather()` for optimization
- Database queries use indexed lookups
- No file operations or blocking I/O

## Security
- Admin access controlled via `is_admin_identity()` utility
- FSM state isolation per user
- Callback data includes database ID (not Telegram ID)
- All user input validated before database insertion

## Customization
- Modify Persian messages in middleware/router
- Adjust button text and emojis
- Change refresh callback logic
- Add channel whitelist/blacklist
