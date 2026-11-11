"""
Ready-made automations. Each template is a flow the runner (services/automation_runner.py)
can execute as-is: the only trigger is `new_customer` (someone writes in for the first time).

Step fields: type (trigger | action | delay | condition), label, sub, icon (builder icon),
messageVariants (text of a message step; {{name}} = the customer's first name), and for the
branches of a "Customer replied?" condition: id "yes" / "no" with sub "YES" / "NO".
New automations start as *Scheduled* — the user reviews the messages, then presses Activate.
"""
import copy


def _trigger():
    return {"id": "trigger", "type": "trigger", "label": "New customer", "sub": "Trigger", "icon": "Zap"}


def _msg(node_id, label, text, sub="Message"):
    return {"id": node_id, "type": "action", "label": label, "sub": sub, "icon": "Send",
            "messageVariants": [text], "assignmentMode": "round_robin"}


def _handoff(node_id="handoff", sub="Action"):
    return {"id": node_id, "type": "action", "label": "Transfer to human", "sub": sub, "icon": "UserCog"}


def _wait(label):
    return {"id": "delay", "type": "delay", "label": label, "sub": "Delay", "icon": "Clock"}


def _replied():
    return {"id": "condition", "type": "condition", "label": "Customer replied?", "sub": "Condition", "icon": "GitBranch"}


TEMPLATES = [
    {
        "id": "welcome",
        "name": "Welcome new customers",
        "description": "Greets everyone who writes to you for the first time, right away.",
        "flow": {"nodes": [
            _trigger(),
            _msg("welcome", "Send welcome message", "Hi {{name}}! Thanks for contacting us. How can we help you today?"),
        ]},
    },
    {
        "id": "welcome_followup",
        "name": "Welcome + follow-up",
        "description": "Welcome message, then after 2 hours: if they have not replied send a follow-up, otherwise hand the chat to your team.",
        "flow": {"nodes": [
            _trigger(),
            _msg("welcome", "Send welcome message", "Hi {{name}}! Thanks for contacting us. How can we help you today?"),
            _wait("Wait 2 hours"),
            _replied(),
            _handoff("yes", "YES"),
            _msg("no", "Send follow-up", "Hi {{name}}, just checking in. Is there anything we can help you with?", "NO"),
        ]},
    },
    {
        "id": "handoff",
        "name": "Hand over to a human",
        "description": "Confirms you got the message and immediately flags the chat for your team to answer.",
        "flow": {"nodes": [
            _trigger(),
            _msg("ack", "Send acknowledgement message", "Thanks {{name}}, a member of our team will reply to you shortly."),
            _handoff(),
        ]},
    },
    {
        "id": "qualify",
        "name": "Qualify new leads",
        "description": "Asks what they need, waits a day, then reminds those who stayed silent and passes engaged leads to your team.",
        "flow": {"nodes": [
            _trigger(),
            _msg("welcome", "Send message: what do you need?", "Hi {{name}}! To help you best, could you tell us what you are looking for and your budget?"),
            _wait("Wait 1 day"),
            _replied(),
            _handoff("yes", "YES"),
            _msg("no", "Send reminder message", "Hi {{name}}, we would love to help. Reply here whenever you are ready.", "NO"),
        ]},
    },
]


def get_template(template_id: str) -> dict | None:
    return next((t for t in TEMPLATES if t["id"] == template_id), None)


def flow_of(template: dict) -> dict:
    return copy.deepcopy(template["flow"])


def steps_of(template: dict) -> list[str]:
    return [n["label"] for n in template["flow"]["nodes"]]


# The flow the builder shows for a new/empty automation (new customer -> welcome -> wait 2 hours ->
# replied? -> YES transfer / NO follow-up). An automation with no saved steps runs exactly this.
DEFAULT_FLOW = flow_of(get_template("welcome_followup"))

# Text used by a message step that has none, picked from the step's label.
_DEFAULT_TEXTS = (
    ("welcome", "Hi {{name}}! Thanks for contacting us. How can we help you today?"),
    ("follow", "Hi {{name}}, just checking in. Is there anything we can help you with?"),
    ("remind", "Hi {{name}}, we would love to help. Reply here whenever you are ready."),
    ("acknowledg", "Thanks {{name}}, a member of our team will reply to you shortly."),
)


def default_message_for(label: str) -> str:
    low = (label or "").lower()
    return next((text for key, text in _DEFAULT_TEXTS if key in low), "Hi {{name}}, thanks for getting in touch.")
