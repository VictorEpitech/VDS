"""Visibilité des incidents et des agents selon le périmètre (lot J)."""

from sqlalchemy import ColumnElement, Text, and_, cast, false, or_, select, true

from vds_server.core.auth import Principal
from vds_server.db.models import Screen
from vds_server.db.models_power import Incident
from vds_server.diffusion.scope import PerimeterScope


def incident_filter(actor: Principal, scope: PerimeterScope) -> ColumnElement[bool]:
    """Un éditeur voit les incidents de ses écrans et de ses agents de site ; jamais ceux des
    modules ni des sites hors périmètre."""
    p = actor.perimeter
    if p.unrestricted:
        return true()
    if p.empty:
        return false()
    screens = select(cast(Screen.id, Text)).where(scope.screen_filter(actor))
    return or_(
        and_(Incident.subject_type == "screen", Incident.subject_id.in_(screens)),
        and_(Incident.subject_type == "agent", Incident.site_id.in_(p.sites)),
    )
