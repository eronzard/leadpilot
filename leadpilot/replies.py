"""Reply drafting from approved templates.

The model only contributes ``personal_note`` (a sentence or two reflecting the lead's problem).
Links, offers and sign-offs come from config, so a reply can never contain an invented price,
a hallucinated URL, or a promise nobody approved.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .models import Decision, LeadIn, Qualification, Reply

TEMPLATES: dict[str, dict[str, dict[str, str]]] = {
    "en": {
        "hot": {"subject": "Re: your automation project",
                "body": "Thanks for the detail - this is exactly the kind of project we take on.\n\n"
                        "The fastest next step is a 30-minute call where we map the process and give you "
                        "a fixed-scope estimate: {booking_link}"},
        "warm": {"subject": "Re: your inquiry",
                 "body": "Thanks for reaching out. To scope this properly, could you share roughly how "
                         "many hours a week the process takes today and when you would like it running?\n\n"
                         "If it is easier to talk it through, you can grab a slot here: {booking_link}"},
        "cold": {"subject": "Re: your inquiry",
                 "body": "Thanks for getting in touch. Based on what you described, we may not be the "
                         "best fit right now, but if your scope or timeline changes we would be glad to "
                         "hear from you again."},
        "support": {"subject": "We received your support request",
                    "body": "Thanks - your request has been logged and our support team will reply within "
                            "one business day. If it is urgent, write to {support_email}."},
        "greeting": "Hi {first_name},",
        "signoff": "Best,\n{sender_name}",
    },
    "es": {
        "hot": {"subject": "Re: su proyecto de automatización",
                "body": "Gracias por los detalles: es exactamente el tipo de proyecto que hacemos.\n\n"
                        "El siguiente paso es una llamada de 30 minutos para mapear el proceso y darle "
                        "una estimación cerrada: {booking_link}"},
        "warm": {"subject": "Re: su consulta",
                 "body": "Gracias por escribirnos. Para estimarlo bien, ¿cuántas horas por semana lleva "
                         "hoy este proceso y para cuándo le gustaría tenerlo funcionando?\n\n"
                         "Si prefiere hablarlo, puede reservar aquí: {booking_link}"},
        "cold": {"subject": "Re: su consulta",
                 "body": "Gracias por contactarnos. Por lo que describe, quizá no seamos la mejor opción "
                         "ahora mismo, pero si cambian el alcance o los plazos estaremos encantados de "
                         "hablar."},
        "support": {"subject": "Hemos recibido su solicitud de soporte",
                    "body": "Gracias: su solicitud está registrada y el equipo de soporte responderá en un "
                            "día hábil. Si es urgente, escriba a {support_email}."},
        "greeting": "Hola {first_name}:",
        "signoff": "Saludos,\n{sender_name}",
    },
    "fr": {
        "hot": {"subject": "Re : votre projet d'automatisation",
                "body": "Merci pour ces précisions : c'est exactement le type de projet que nous menons.\n\n"
                        "Prochaine étape : un appel de 30 minutes pour cartographier le processus et vous "
                        "donner une estimation au forfait : {booking_link}"},
        "warm": {"subject": "Re : votre demande",
                 "body": "Merci de nous avoir contactés. Pour bien chiffrer, combien d'heures par semaine "
                         "ce processus prend-il aujourd'hui, et pour quand le souhaitez-vous ?\n\n"
                         "Vous pouvez aussi réserver un créneau ici : {booking_link}"},
        "cold": {"subject": "Re : votre demande",
                 "body": "Merci de votre message. D'après votre description, nous ne sommes peut-être pas "
                         "le bon partenaire pour l'instant, mais n'hésitez pas à revenir vers nous si le "
                         "périmètre ou le calendrier évolue."},
        "support": {"subject": "Nous avons bien reçu votre demande",
                    "body": "Merci : votre demande est enregistrée et l'équipe support vous répondra sous un "
                            "jour ouvré. En cas d'urgence, écrivez à {support_email}."},
        "greeting": "Bonjour {first_name},",
        "signoff": "Cordialement,\n{sender_name}",
    },
}


def draft_reply(lead: LeadIn, q: Qualification, decision: Decision, business: dict[str, Any]) -> Optional[Reply]:
    """Return a reply for tiers that get one; spam and 'other' get none."""
    if decision.tier not in {"hot", "warm", "cold", "support"}:
        return None
    lang = q.language if q.language in TEMPLATES else "en"
    t = TEMPLATES[lang]
    b = business["business"]
    first_name = lead.name.split()[0] if lead.name.strip() else ""
    fields = {"booking_link": b["booking_link"], "support_email": b["support_email"],
              "sender_name": b["sender_name"], "first_name": first_name}

    # "Hi ," -> "Hi," when the form had no name.
    parts = [re.sub(r"\s+([,:])", r"\1", t["greeting"].format(**fields))]
    note = q.personal_note.strip()
    if note:
        parts.append(note)
    parts.append(t[decision.tier]["body"].format(**fields))
    parts.append(t["signoff"].format(**fields))
    return Reply(subject=t[decision.tier]["subject"], body="\n\n".join(parts))
