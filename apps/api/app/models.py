import os
from sqlalchemy import create_engine, Column, String, JSON, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./finflow.db')
engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {})
Session = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()

class Record(Base):
    __tablename__ = 'records'
    id = Column(String, primary_key=True)
    kind = Column(String, nullable=False, index=True)
    merchant_id = Column(String, nullable=False, index=True)
    payload = Column(JSON, nullable=False)

class Action(Base):
    __tablename__ = 'actions'
    id = Column(String, primary_key=True)
    merchant_id = Column(String, nullable=False)
    case_id = Column(String, nullable=False)
    idempotency_key = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint('merchant_id', 'idempotency_key'),)
