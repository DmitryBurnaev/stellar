"""Inventory relationships and authorization metadata."""

import sqlalchemy as sa
from sqlalchemy.orm import Session

from src.db.models import Application, BaseModel, Provider, Server, ServerDomain, ServerGroup
from src.modules.admin.app import INVENTORY_VIEWS
from src.modules.auth.permissions import Permission, has_permission
from src.db.models import User


def test_inventory_relationships() -> None:
    engine = sa.create_engine("sqlite://")
    BaseModel.metadata.create_all(engine)
    with Session(engine) as session:
        provider = Provider(name="Example")
        group = ServerGroup(name="Production")
        app = Application(name="Database")
        server = Server(ip_address="192.0.2.1", ssh_alias="example", provider=provider)
        server.groups.append(group)
        server.applications.append(app)
        server.domains.append(ServerDomain(name="example.test"))
        session.add(server)
        session.commit()
        session.expire_all()
        loaded = session.scalars(sa.select(Server)).one()
        assert loaded.provider.name == "Example"
        assert [item.name for item in loaded.groups] == ["Production"]
        assert [item.name for item in loaded.applications] == ["Database"]
        assert [item.name for item in loaded.domains] == ["example.test"]
    engine.dispose()


def test_inventory_admin_requires_grant() -> None:
    assert all(view.required_permission == Permission.INVENTORY_ADMIN for view in INVENTORY_VIEWS)
    user = User(username="operator", password="unused", is_active=True, is_admin=False)
    assert not has_permission(user, Permission.INVENTORY_ADMIN)
    user.can_manage_inventory = True
    assert has_permission(user, Permission.INVENTORY_ADMIN)
    user.is_active = False
    assert not has_permission(user, Permission.INVENTORY_ADMIN)
