import uuid
from datetime import datetime

from pydantic import BaseModel, model_validator

from app.models.automation import AutomationStatus


TRIGGERS = ("new_customer", "keyword")


def _check_trigger(trigger_type, config):
    if trigger_type is not None and trigger_type not in TRIGGERS:
        raise ValueError(f"trigger_type must be one of {', '.join(TRIGGERS)}")
    if trigger_type == "keyword":
        words = [w.strip() for w in ((config or {}).get("keywords") or []) if isinstance(w, str) and w.strip()]
        if not words:
            raise ValueError("A keyword automation needs at least one keyword")
        if len(words) > 20:
            raise ValueError("At most 20 keywords")
        config["keywords"] = words
    return config


class AutomationCreate(BaseModel):
    name: str
    trigger_type: str = "new_customer"
    trigger_config: dict | None = None
    flow: dict = {}

    @model_validator(mode="after")
    def _validate_trigger(self):
        self.trigger_config = _check_trigger(self.trigger_type, self.trigger_config)
        return self


class AutomationUpdate(BaseModel):
    name: str | None = None
    flow: dict | None = None
    status: AutomationStatus | None = None
    trigger_type: str | None = None
    trigger_config: dict | None = None

    @model_validator(mode="after")
    def _validate_trigger(self):
        if self.trigger_type is not None:
            self.trigger_config = _check_trigger(self.trigger_type, self.trigger_config)
        return self


class AutomationOut(BaseModel):
    id: uuid.UUID
    name: str
    admin_id: uuid.UUID
    trigger_type: str
    trigger_config: dict | None = None
    flow: dict
    status: AutomationStatus
    recipients: int
    sent: int
    delivered: int
    replies: int
    created_at: datetime

    class Config:
        from_attributes = True


class AutomationTemplateOut(BaseModel):
    id: str
    name: str
    description: str
    starts_when: str
    steps: list[str]


class AutomationFromTemplate(BaseModel):
    template_id: str
    name: str | None = None
