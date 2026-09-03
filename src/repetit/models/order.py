"""Модель заявки repetit.ru (из батча GET /lk/api/teacher/orders?ids=, RECON §3)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Order:
    id: int
    subject: str
    subject_id: int | None
    purpose: str
    information: str
    min_price: int | None
    max_price: int | None
    contact_name: str | None
    city: str | None
    metro: str | None
    lesson_place: int | None  # int-enum площадки; 4 = онлайн (RECON §3)
    pupil_category: str | None
    order_date: str | None
    additions: list[str]
    divisions: list[str]
    raw: dict

    @property
    def title(self) -> str:
        parts = [x for x in [", ".join(self.additions[:3]), ", ".join(self.divisions[:2])] if x]
        return f"{self.subject}: {'; '.join(parts)}" if parts else self.subject

    @property
    def searchable(self) -> str:
        return " \n ".join(
            [self.subject, self.purpose or "", self.information or "", *self.additions, *self.divisions]
        )

    @property
    def is_remote(self) -> bool:
        return self.lesson_place == 4

    @classmethod
    def from_api(cls, d: dict) -> "Order":
        def _names(v):
            if not isinstance(v, list):
                return []
            return [str(i.get("name")).strip() for i in v if isinstance(i, dict) and i.get("name")]

        subject = d.get("subject") or {}
        area = d.get("area") or {}
        return cls(
            id=int(d.get("id") or 0),
            subject=str(subject.get("name") or ""),
            subject_id=subject.get("id"),
            purpose=str(d.get("purpose") or ""),
            information=str(d.get("information") or ""),
            min_price=d.get("minPrice"),
            max_price=d.get("maxPrice"),
            contact_name=d.get("contactName"),
            city=area.get("cityName"),
            metro=d.get("homeMetroName"),
            lesson_place=d.get("lessonPlace"),
            pupil_category=(d.get("pupilCategory") or {}).get("name"),
            order_date=d.get("orderDate"),
            additions=_names(d.get("subjectAdditions")),
            divisions=_names(d.get("subjectDivisions")),
            raw=d,
        )

    def triage_dict(self) -> dict:
        """Компактная проекция для LLM (без raw-мусора)."""
        return {
            "id": self.id,
            "subject": self.subject,
            "goal": self.purpose,
            "client_text": self.information,
            "price_rub_per_60min": [self.min_price, self.max_price],
            "client_name": self.contact_name,
            "city": self.city,
            "metro": self.metro,
            "online": self.is_remote,
            "pupil_category": self.pupil_category,
            "additions": self.additions,
            "divisions": self.divisions,
        }
