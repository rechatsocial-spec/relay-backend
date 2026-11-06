"""
Populates the database with data shaped like the frontend's mock data, so
the two can be swapped against each other directly. Run with:
    python -m app.seed
"""
import random
from datetime import datetime, timedelta, timezone

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import engine, SessionLocal
from app.models.user import User, Role
from app.models.whatsapp_number import WhatsAppNumber, NumberStatus
from app.models.contact import Contact, ContactStatus
from app.models.conversation import Conversation, Message, ConversationStatus, MessageSender, MessageType
from app.models.campaign import Campaign, CampaignStatus, MessageContentType
from app.models.automation import Automation, AutomationStatus
from app.models.chatbot import Chatbot, ChatbotStatus
from app.models.api_key import ApiKey, ApiKeyStatus
from app.core.crypto import encrypt_secret, mask

FIRST = ["Rahul", "Priya", "Ananya", "Vikram", "Neha", "Karan", "Ayesha", "Rohan", "Sanya", "Arjun", "Meera", "Aditya"]
LAST = ["Sharma", "Nair", "Mehta", "Reddy", "Kapoor", "Iyer", "Joshi", "Malhotra", "Verma", "Singh"]
CITIES = ["Mumbai", "Pune", "Bengaluru", "Delhi", "Hyderabad", "Chennai", "Nagpur", "Ahmedabad"]
TAGS = ["VIP", "Repeat Buyer", "Cold Lead", "Hot Lead", "Wholesale", "Referral"]
CONTACT_STATUSES = list(ContactStatus)
NUMBER_STATUSES = [NumberStatus.ACTIVE] * 4 + [NumberStatus.WARNING] * 2 + [NumberStatus.ERROR, NumberStatus.INACTIVE]


def run():
    Base.metadata.create_all(bind=engine)
    from app.db.auto_migrate import upgrade_schema
    upgrade_schema(engine)
    db = SessionLocal()

    # Any user at all = already set up. (Checking one specific email broke
    # restarts after that demo account's email was changed.)
    if db.query(User).first():
        print("Already seeded — skipping. Use run_dev_sqlite.py --reset to start fresh.")
        db.close()
        return

    # --- users -----------------------------------------------------------
    super_admin = User(
        name="Deepa Patil", email="deepa@relaycrm.io", phone="+91 9820011234",
        hashed_password=hash_password("password123"), role=Role.SUPER_ADMIN,
    )
    db.add(super_admin)

    admins = []
    for i in range(12):
        name = f"{FIRST[i % len(FIRST)]} {LAST[(i * 3) % len(LAST)]}"
        a = User(
            name=name, email=f"{name.lower().replace(' ', '.')}@relaycrm.io",
            phone=f"+91 9{800000000 + i * 7654321}"[:14],
            hashed_password=hash_password("password123"), role=Role.ADMIN,
        )
        admins.append(a)
        db.add(a)
    db.flush()

    # --- whatsapp numbers --------------------------------------------------
    numbers = []
    for i in range(47):
        admin = admins[i % len(admins)]
        status = NUMBER_STATUSES[i % len(NUMBER_STATUSES)]
        n = WhatsAppNumber(
            phone=f"+91 9{700000000 + i * 913}"[:14],
            display_name=f"{admin.name.split()[0]} — {['Sales','Support','Orders','Broadcast'][i % 4]} Line",
            admin_id=admin.id, status=status,
            messages_today=0 if status == NumberStatus.INACTIVE else 120 + (i * 53) % 1400,
            delivery_rate=40 + (i % 20) if status == NumberStatus.ERROR else 88 + (i % 11),
            reply_rate=8 + (i % 22),
            last_activity_at=datetime.now(timezone.utc) - timedelta(minutes=i * 3),
        )
        numbers.append(n)
        db.add(n)
    db.flush()

    # --- contacts ------------------------------------------------------
    contacts = []
    for i in range(86):
        name = f"{FIRST[i % len(FIRST)]} {LAST[(i * 5) % len(LAST)]}"
        admin = admins[i % len(admins)]
        number = numbers[i % len(numbers)]
        c = Contact(
            name=name, phone=f"+91 9{600000000 + i * 4111}"[:14],
            email=f"{name.lower().replace(' ', '')}@mail.com", city=CITIES[i % len(CITIES)],
            whatsapp_number_id=number.id, admin_id=admin.id,
            status=CONTACT_STATUSES[i % len(CONTACT_STATUSES)],
            tags=random.sample(TAGS, 2),
            last_contact_at=datetime.now(timezone.utc) - timedelta(days=i % 27),
        )
        contacts.append(c)
        db.add(c)
    db.flush()

    # --- a handful of inbox conversations -------------------------------
    convo_statuses = [ConversationStatus.NEEDS_HUMAN, ConversationStatus.BOT, ConversationStatus.HUMAN]
    for i in range(15):
        contact = contacts[i]
        conv = Conversation(
            contact_id=contact.id, whatsapp_number_id=contact.whatsapp_number_id, admin_id=contact.admin_id,
            status=convo_statuses[i % 3], unread_count=i % 3,
        )
        db.add(conv)
        db.flush()
        db.add(Message(conversation_id=conv.id, sender=MessageSender.CUSTOMER, type=MessageType.TEXT,
                        text="Hi, I had a question about my order.", status="delivered"))
        db.add(Message(conversation_id=conv.id, sender=MessageSender.BOT, type=MessageType.TEXT,
                        text="Hi! Let me check that for you.", status="delivered"))

    # --- campaigns -------------------------------------------------------
    campaign_names = ["Diwali Offers Broadcast", "New Collection Launch", "Weekend Flash Sale",
                       "Festive Season Broadcast", "Wholesale Buyer Outreach", "Re-engagement — Cold Leads"]
    campaign_statuses = [CampaignStatus.COMPLETED, CampaignStatus.COMPLETED, CampaignStatus.RUNNING,
                          CampaignStatus.SCHEDULED, CampaignStatus.DRAFT, CampaignStatus.FAILED]
    for i, name in enumerate(campaign_names):
        recipients = [5200, 3100, 2400, 5200, 640, 900][i]
        sent = recipients if campaign_statuses[i] == CampaignStatus.COMPLETED else recipients // 2 if campaign_statuses[i] == CampaignStatus.RUNNING else 0
        db.add(Campaign(
            name=name, whatsapp_number_id=numbers[i].id, admin_id=numbers[i].admin_id,
            message_type=MessageContentType.TEXT, message_text="Check out our latest offers!",
            status=campaign_statuses[i], recipients=recipients, sent=sent,
            delivered=round(sent * 0.95), read=round(sent * 0.6), replies=round(sent * 0.08), failed=round(sent * 0.05),
        ))

    # --- automations -------------------------------------------------------
    automation_defs = [
        ("Welcome — New Customer", AutomationStatus.RUNNING, 1240, 1240, 1198, 214),
        ("Order Shipped Notice", AutomationStatus.RUNNING, 860, 860, 831, 92),
        ("Cart Abandonment Follow-up", AutomationStatus.PAUSED, 430, 210, 201, 18),
        ("Festive Season Broadcast", AutomationStatus.SCHEDULED, 5200, 0, 0, 0),
        ("No-Response Nudge (48h)", AutomationStatus.FAILED, 300, 88, 60, 4),
    ]
    for i, (name, status, recipients, sent, delivered, replies) in enumerate(automation_defs):
        db.add(Automation(
            name=name, admin_id=admins[i % len(admins)].id, trigger_type="new_customer",
            flow={"nodes": [{"type": "trigger", "label": "New customer"}, {"type": "action", "label": "Send welcome message"}]},
            status=status, recipients=recipients, sent=sent, delivered=delivered, replies=replies,
        ))

    # --- chatbots -------------------------------------------------------
    bot_defs = [("Support Bot", ChatbotStatus.ACTIVE, 1840, 1520, 220), ("Sales Assistant", ChatbotStatus.ACTIVE, 960, 780, 90),
                ("Order Tracking Bot", ChatbotStatus.ACTIVE, 2210, 2040, 60), ("FAQ Bot (Beta)", ChatbotStatus.PAUSED, 140, 110, 12)]
    for i, (name, status, convs, autoresp, handoffs) in enumerate(bot_defs):
        db.add(Chatbot(
            name=name, whatsapp_number_id=numbers[i].id, admin_id=numbers[i].admin_id,
            flow={"start": "welcome", "menu": ["Product Information", "Pricing", "FAQ", "Talk to Human"]},
            keywords=["hi", "hello", "order status", "price"], status=status,
            conversations=convs, automated_responses=autoresp, human_handoffs=handoffs,
        ))

    # --- api keys -------------------------------------------------------
    for provider, raw in [("Meta Cloud API", "EAAGmock000000000000000000008F29"), ("360dialog BSP", "d360mock0000000000000000000011A4")]:
        db.add(ApiKey(
            provider=provider, masked=mask(raw), encrypted_value=encrypt_secret(raw),
            status=ApiKeyStatus.CONNECTED, updated_by=super_admin.id,
        ))

    db.commit()
    print(f"Seeded: 1 super admin, {len(admins)} admins, {len(numbers)} numbers, {len(contacts)} contacts, "
          f"15 conversations, {len(campaign_names)} campaigns, {len(automation_defs)} automations, {len(bot_defs)} chatbots.")
    print("Login — super admin: deepa@relaycrm.io / password123")
    print(f"Login — admin: {admins[0].email} / password123")


if __name__ == "__main__":
    run()
