from aiogram.fsm.state import State, StatesGroup


class ChannelSetup(StatesGroup):
    reference = State()
    invite = State()
