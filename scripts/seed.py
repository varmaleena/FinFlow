from apps.api.app.main import Base, engine, Session, seed
Base.metadata.create_all(engine)
with Session() as db: seed(db)
print('Synthetic financial evidence seeded.')
