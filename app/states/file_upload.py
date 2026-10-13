from aiogram.fsm.state import State, StatesGroup


class FileUpload(StatesGroup):
    single = State()
    batch = State()
    imp = State()
    search = State()
    dbch = State()
