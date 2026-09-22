from aiogram.fsm.state import State, StatesGroup


class AdminAddPlanStates(StatesGroup):
    title = State()
    description = State()
    duration_days = State()
    volume_gb = State()
    price = State()
    sort_order = State()
    confirm = State()


class AdminEditPlanStates(StatesGroup):
    value = State()


class AdminAddTestAccountStates(StatesGroup):
    title = State()
    description = State()
    config_link = State()
    subscription_link = State()
    duration_hours = State()
    max_claims = State()
    confirm = State()


class AdminEditTestAccountStates(StatesGroup):
    value = State()


class AdminSearchStates(StatesGroup):
    query = State()                   # if you had this
    waiting_user_query = State()      # if you had this
    waiting_service_query = State()   # <--- ADD THIS LINE
    user_query = State()
    service_query = State()
    affiliate_user_query = State()


class AdminWalletAdjustStates(StatesGroup):
    amount = State()


class AdminWithdrawalStates(StatesGroup):
    reject_reason = State()


class AdminServiceEditStates(StatesGroup):
    value = State()


class AdminBroadcastStates(StatesGroup):
    text = State()
    confirm = State()


class AdminSettingsStates(StatesGroup):
    value = State()


class AdminInventoryAddStates(StatesGroup):
    config_link = State()
    subscription_link = State()
    note = State()


class AdminInventoryBulkStates(StatesGroup):
    text = State()


class AdminInventorySearchStates(StatesGroup):
    query = State()


class AdminInventoryEditStates(StatesGroup):
    value = State()
