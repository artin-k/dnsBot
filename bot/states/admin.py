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
    user_query = State()
    service_query = State()
    affiliate_user_query = State()


class AdminWalletAdjustStates(StatesGroup):
    amount = State()


class AdminServiceEditStates(StatesGroup):
    value = State()


class AdminBroadcastStates(StatesGroup):
    text = State()
    confirm = State()


class AdminSettingsStates(StatesGroup):
    value = State()
