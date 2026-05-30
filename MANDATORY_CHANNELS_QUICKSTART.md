# Quick Start: Mandatory Channel Join System

## Step 1: Database Setup
```bash
# Run migration to create mandatory_channels table
alembic upgrade head
```

## Step 2: System is Ready!
The middleware and admin router are already integrated in `loader.py`.

## Admin Commands

### List/Manage Channels
```
/admin_channels
```
Shows all mandatory channels with delete buttons and add button.

### Add a New Channel
1. Send: `/admin_channels`
2. Click: `➕ افزودن کانال جدید`
3. Enter channel ID (e.g., `-1001234567890`)
4. Enter display name (e.g., `کانال اصلی`)
5. Enter invite link (e.g., `https://t.me/mychannel`)

### Delete a Channel
1. Send: `/admin_channels`
2. Click: `❌ Channel Name`

## User Experience

### When User Doesn't Have All Channels:
- **Sees**: "❌ برای استفاده از ربات، باید در کانال‌های زیر عضو شوید:"
- **Gets**: Buttons with invite links for each missing channel
- **Can**: Click `🔄 بررسی مجدد` to refresh after joining

### When User Has All Channels:
- **Gets**: Full access to all bot commands
- **Can**: Use bot normally

## Technical Details

### Files Modified/Created:
1. `app/models.py` - Added MandatoryChannel model
2. `app/repositories/mandatory_channels.py` - NEW
3. `bot/middlewares/mandatory_channels.py` - NEW
4. `bot/routers/mandatory_channels.py` - NEW
5. `bot/loader.py` - Updated to register middleware/router
6. `alembic/versions/20260530_0008_mandatory_channels.py` - NEW

### How It Works:
1. **Middleware** intercepts private messages
2. **Fetches** all active channels from database
3. **Checks** user membership using `bot.get_chat_member()`
4. **Blocks** messages if user missing any channel
5. **Shows** join buttons and refresh option

### Async/Error Handling:
- ✅ All async/await properly implemented
- ✅ Graceful error handling with logging
- ✅ Handles bot removal from channels
- ✅ Safe defaults for API failures

## Configuration

### Disable for Testing:
Comment out line in `bot/loader.py`:
```python
# mandatory_join_middleware = DynamicMandatoryJoinMiddleware()
# dp.update.middleware(mandatory_join_middleware)
```

### Admin Access:
Uses existing `is_admin_identity()` utility and `settings.admin_ids`.

## Persian UI Strings

| Element | Text |
|---------|------|
| Missing channels message | ❌ برای استفاده از ربات، باید در کانال‌های زیر عضو شوید: |
| Add button | ➕ افزودن کانال جدید |
| Delete button | ❌ {channel_name} |
| Refresh button | 🔄 بررسی مجدد |
| Success | ✅ شما در تمام کانال‌های اجباری عضو هستید! |

## Troubleshooting

### "Bot is not a member of channel"
- Make sure bot is added to all mandatory channels with admin rights

### "Channel not found"
- Verify channel ID is correct (must be negative number like -1001234567890)

### Users can't join channels
- Check invite link is correct
- Verify channel is public or has link sharing enabled

### Migration fails
- Ensure database user has ALTER TABLE permissions
- Check for conflicting table names

## Next Steps (Optional)
1. Add channel image/description to UI
2. Implement channel activity checking (kick inactive members)
3. Add temporary channel exemptions
4. Create analytics dashboard
5. Add notification when user joins channel
