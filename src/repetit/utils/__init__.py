"""Общие помощники: темп действий, анти-инъекция."""

from repetit.utils.pacing import human_pause, type_human
from repetit.utils.textguard import has_contacts
from repetit.utils.workhours import in_work_hours

__all__ = ["has_contacts", "human_pause", "in_work_hours", "type_human"]
