from aiogram.fsm.state import State, StatesGroup


class FileUpload(StatesGroup):
    search = State()
    dbch = State()
    duration = State()
