from aiogram.fsm.state import State, StatesGroup


class BuyStates(StatesGroup):
    waiting_username = State()
    waiting_discount_code = State()
    waiting_receipt = State()
    waiting_receipt_selection = State()
    waiting_manual_ip = State() 
