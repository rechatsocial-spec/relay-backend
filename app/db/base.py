from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Import every model here so Base.metadata knows about all tables before
# create_all() runs in main.py / seed.py. Add new models to this list.
from app.models.user import User  # noqa: E402,F401
from app.models.whatsapp_number import WhatsAppNumber  # noqa: E402,F401
from app.models.contact import Contact  # noqa: E402,F401
from app.models.conversation import Conversation, Message  # noqa: E402,F401
from app.models.campaign import Campaign  # noqa: E402,F401
from app.models.automation import Automation, AutomationRun  # noqa: E402,F401
from app.models.chatbot import Chatbot  # noqa: E402,F401
from app.models.api_key import ApiKey  # noqa: E402,F401
from app.models.activity_log import ActivityLog  # noqa: E402,F401
from app.models.media import MediaFile  # noqa: E402,F401
from app.models.template import MessageTemplate  # noqa: E402,F401
