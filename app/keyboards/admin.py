from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as M

from app.keyboards.pagination import pager_row


def kb(*rows) -> M:
    return M(inline_keyboard=[list(r) for r in rows])


def btn(text: str, data: str) -> B:
    assert len(data.encode()) <= 64, data
    return B(text=text, callback_data=data)


BACK_HOME = btn("↩️ Back", "a:home")


def dashboard() -> M:
    return kb([btn("📂 Files", "a:f"), btn("📢 Channels", "a:c")],
              [btn("📣 Broadcast", "a:b"), btn("⚙️ Settings", "a:t")],
              [btn("📊 Statistics", "a:s"), btn("❌ Close", "a:close")])


def files_menu() -> M:
    return kb([btn("➕ Add File", "a:f:add"), btn("📦 Batch Add", "a:f:batch")],
              [btn("🔗 Generate Links", "a:f:gen:0"), btn("📋 Stored Files", "a:f:list:0")],
              [btn("🔍 Search Files", "a:f:search"), btn("📥 Import", "a:f:imp")],
              [btn("🔒 Content Protection", "a:f:prot")], [BACK_HOME])


def cancel(to: str = "a:home") -> M:
    return kb([btn("✖️ Cancel", to)])


def channels_menu() -> M:
    return kb([btn("➕ Add Channel", "a:c:add"), btn("📋 Manage Channels", "a:c:list:0")],
              [btn("📊 Channel Statistics", "a:c:stats"), btn("🎚️ Required Steps", "a:c:steps")],
              [btn("⚖️ Balancing Settings", "a:c:bal")], [BACK_HOME])


def settings_menu() -> M:
    return kb([btn("🖼️ Welcome Image", "a:t:wi"), btn("✏️ Welcome Text", "a:t:wt")],
              [btn("🔘 Welcome Buttons", "a:t:wb"), btn("📢 Official Channels", "a:t:oc")],
              [btn("🎬 More Videos URL", "a:t:mv"), btn("👨‍💻 Developer URL", "a:t:dv")],
              [btn("ℹ️ About Text", "a:t:ab"), btn("🔐 Default Protection", "a:t:prot")],
              [btn("🧹 Auto-delete Timer", "a:t:ad"), btn("🌐 Language Options", "a:t:lang")], [BACK_HOME])


def confirm(yes: str, no: str, label: str = "✅ Confirm") -> M:
    return kb([btn(label, yes), btn("✖️ Cancel", no)])
