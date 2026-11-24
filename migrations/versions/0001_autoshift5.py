"""AutoShift 5 initial normalized schema and immutable schedule guards."""
from alembic import op
from autoshift5.models import Base
from autoshift5.db import install_guards
revision = '0001'
down_revision = None


def upgrade():
    Base.metadata.create_all(op.get_bind())
    install_guards(op.get_bind())


def downgrade():
    raise RuntimeError('Destructive downgrade disabled. Restore a verified database backup instead.')
