from aiogram.fsm.state import State, StatesGroup


class VerificationStates(StatesGroup):
    waiting_contact = State()


class WalletStates(StatesGroup):
    waiting_topup_amount = State()
    waiting_topup_receipt = State()
