"""
Многие методы бота (например, DocumentHandler._start_doc) написаны в расчёте
на callback_query от нажатия кнопки — у него есть .edit_message_text().
Разговорный агент и некоторые команды (/profile) вызывают эти методы не из
нажатия кнопки, а из обычного текстового сообщения — у него такого метода нет,
только .reply_text(). Этот адаптер даёт обычному сообщению нужный интерфейс,
чтобы не дублировать логику генерации документов ещё раз.
"""

class MessageQueryAdapter:
    def __init__(self, message):
        self.message = message

    async def answer(self, *args, **kwargs):
        # У callback_query.answer() нет прямого аналога у обычных сообщений — просто игнорируем
        pass

    async def edit_message_text(self, text, reply_markup=None, parse_mode=None):
        return await self.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
