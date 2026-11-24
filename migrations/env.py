from alembic import context
from autoshift5.db import make_engine, DEFAULT_DB
from autoshift5.models import Base

config = context.config
target_metadata = Base.metadata
if context.is_offline_mode():
    context.configure(url=config.get_main_option('sqlalchemy.url'), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = make_engine(config.attributes.get('db_path', DEFAULT_DB))
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
