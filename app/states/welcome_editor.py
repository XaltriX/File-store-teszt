from aiogram.fsm.state import State, StatesGroup


class SettingsInput(StatesGroup):
    value = State()  # FSM data: {"field": str}
