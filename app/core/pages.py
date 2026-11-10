"""Sidebar pages a Super Admin can show/hide for each Admin."""

# Pages an Admin can see only if the Super Admin switches them on.
DEFAULT_HIDDEN_PAGES = ["dashboard", "chatbot", "settings"]
# Pages that can be toggled (admins/activity are super-admin-only already).
TOGGLEABLE_PAGES = ["dashboard", "inbox", "numbers", "crm", "campaigns",
                    "automations", "chatbot", "analytics", "settings"]


def hidden_pages_for(user) -> list[str]:
    from app.models.user import Role
    if user.role == Role.SUPER_ADMIN:
        return []
    if user.hidden_pages is None:
        return list(DEFAULT_HIDDEN_PAGES)
    return [p for p in user.hidden_pages.split(",") if p]
