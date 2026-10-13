from aiogram.fsm.state import State, StatesGroup


class BroadcastFlow(StatesGroup):
    message = State()
